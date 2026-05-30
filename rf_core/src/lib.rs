/*!
╔══════════════════════════════════════════════════════════════════╗
║              rf_core — Rust acceleration module                  ║
║  Called from processor.py via PyO3 when compiled with maturin   ║
╚══════════════════════════════════════════════════════════════════╝

Build:
    cd rf_core
    maturin develop --release

This exposes three functions to Python:
    rf_core.compute_grid(...)   → interpolated heatmap grid (fast)
    rf_core.filter_packets(...) → batch MAC filter
    rf_core.parse_packets(...)  → batch CSV line parser
*/

use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use std::collections::HashSet;


// ══════════════════════════════════════════════
//  GRID COMPUTATION
//  Replaces scipy griddata + gaussian_filter
//  ~10-30x faster for large packet histories
// ══════════════════════════════════════════════

/// Compute interpolated 2D heatmap grid from scattered (time, channel, rssi) points.
///
/// Args:
///     elapsed:     array of elapsed time values (x axis)
///     channels:    array of channel numbers (y axis)
///     rssis:       array of RSSI values (z values)
///     resolution:  grid cells per axis
///     ch_min:      minimum channel (usually 1)
///     ch_max:      maximum channel (usually 11)
///     rssi_min:    minimum RSSI clamp value (usually -95)
///     sigma:       gaussian smoothing radius
///
/// Returns:
///     (xi, yi, zi) as flat Vec<f64> — Python reshapes these into 2D arrays
#[pyfunction]
fn compute_grid(
    py: Python,
    elapsed: Vec<f64>,
    channels: Vec<f64>,
    rssis: Vec<f64>,
    resolution: usize,
    ch_min: f64,
    ch_max: f64,
    rssi_min: f64,
    sigma: f64,
) -> PyResult<(Vec<Vec<f64>>, Vec<Vec<f64>>, Vec<Vec<f64>>)> {
    let n = elapsed.len();
    if n < 4 {
        return Err(PyValueError::new_err("Need at least 4 data points"));
    }
    if channels.len() != n || rssis.len() != n {
        return Err(PyValueError::new_err("Input arrays must have equal length"));
    }

    let t_min = elapsed.iter().cloned().fold(f64::INFINITY, f64::min);
    let t_max = elapsed.iter().cloned().fold(f64::NEG_INFINITY, f64::max);

    if (t_max - t_min).abs() < 1e-9 {
        return Err(PyValueError::new_err("Time range too small"));
    }

    // Build output grid axes
    let mut xi_row = Vec::with_capacity(resolution);
    let mut yi_row = Vec::with_capacity(resolution);
    for i in 0..resolution {
        let t  = t_min  + (t_max  - t_min)  * (i as f64) / (resolution as f64 - 1.0);
        let ch = ch_min + (ch_max - ch_min) * (i as f64) / (resolution as f64 - 1.0);
        xi_row.push(t);
        yi_row.push(ch);
    }

    // Interpolate using inverse distance weighting (IDW)
    // Fast alternative to scipy's cubic griddata
    let mut zi = vec![vec![rssi_min; resolution]; resolution];

    for (row_idx, &ch_val) in yi_row.iter().enumerate() {
        for (col_idx, &t_val) in xi_row.iter().enumerate() {
            let mut weight_sum = 0.0_f64;
            let mut value_sum  = 0.0_f64;

            for i in 0..n {
                let dt  = (elapsed[i] - t_val)  / (t_max - t_min).max(1e-9);
                let dch = (channels[i] - ch_val) / (ch_max - ch_min).max(1e-9);
                let dist_sq = dt * dt + dch * dch;

                if dist_sq < 1e-12 {
                    // Exact match — use the value directly
                    value_sum  = rssis[i];
                    weight_sum = 1.0;
                    break;
                }

                // Power parameter p=2 (standard IDW)
                let w = 1.0 / dist_sq;
                weight_sum += w;
                value_sum  += w * rssis[i];
            }

            if weight_sum > 0.0 {
                zi[row_idx][col_idx] = value_sum / weight_sum;
            }
        }
    }

    // Gaussian smoothing pass
    let sigma_cells = (sigma * resolution as f64 / 11.0).max(1.0) as usize;
    let zi_smoothed = gaussian_blur_2d(&zi, sigma_cells, resolution);

    // Clamp to RSSI range
    let rssi_max = -30.0_f64;
    let zi_clamped: Vec<Vec<f64>> = zi_smoothed.iter().map(|row| {
        row.iter().map(|&v| v.clamp(rssi_min, rssi_max)).collect()
    }).collect();

    // Build meshgrid xi, yi
    let xi: Vec<Vec<f64>> = (0..resolution).map(|_| xi_row.clone()).collect();
    let yi: Vec<Vec<f64>> = (0..resolution).map(|r| {
        vec![yi_row[r]; resolution]
    }).collect();

    Ok((xi, yi, zi_clamped))
}


/// Simple separable gaussian blur on a 2D grid.
/// Runs two 1D passes (horizontal then vertical) — O(n * k) not O(n * k^2).
fn gaussian_blur_2d(grid: &Vec<Vec<f64>>, radius: usize, size: usize) -> Vec<Vec<f64>> {
    if radius == 0 {
        return grid.clone();
    }

    let kernel = gaussian_kernel_1d(radius);
    let k      = kernel.len();
    let half   = k / 2;

    // Horizontal pass
    let mut h_pass = vec![vec![0.0_f64; size]; size];
    for r in 0..size {
        for c in 0..size {
            let mut acc = 0.0;
            let mut wt  = 0.0;
            for (ki, &kv) in kernel.iter().enumerate() {
                let cc = c as isize + ki as isize - half as isize;
                if cc >= 0 && cc < size as isize {
                    acc += grid[r][cc as usize] * kv;
                    wt  += kv;
                }
            }
            h_pass[r][c] = if wt > 0.0 { acc / wt } else { grid[r][c] };
        }
    }

    // Vertical pass
    let mut v_pass = vec![vec![0.0_f64; size]; size];
    for r in 0..size {
        for c in 0..size {
            let mut acc = 0.0;
            let mut wt  = 0.0;
            for (ki, &kv) in kernel.iter().enumerate() {
                let rr = r as isize + ki as isize - half as isize;
                if rr >= 0 && rr < size as isize {
                    acc += h_pass[rr as usize][c] * kv;
                    wt  += kv;
                }
            }
            v_pass[r][c] = if wt > 0.0 { acc / wt } else { h_pass[r][c] };
        }
    }

    v_pass
}

/// Build a 1D gaussian kernel of given radius.
fn gaussian_kernel_1d(radius: usize) -> Vec<f64> {
    let size   = radius * 2 + 1;
    let sigma  = radius as f64 / 2.0;
    let mut k: Vec<f64> = (0..size).map(|i| {
        let x = i as f64 - radius as f64;
        (-x * x / (2.0 * sigma * sigma)).exp()
    }).collect();
    let sum: f64 = k.iter().sum();
    k.iter_mut().for_each(|v| *v /= sum);
    k
}


// ══════════════════════════════════════════════
//  BATCH MAC FILTER
//  Faster than Python set lookups for large batches
// ══════════════════════════════════════════════

/// Filter a batch of MAC addresses against a whitelist and blacklist.
///
/// Args:
///     macs:           list of MAC address strings
///     whitelist:      set of allowed MACs (empty = allow all)
///     blacklist:      set of blocked MACs
///     whitelist_mode: if True, only whitelisted MACs pass
///
/// Returns:
///     Vec<bool> — true if the MAC at that index should be allowed
#[pyfunction]
fn filter_packets(
    macs: Vec<String>,
    whitelist: Vec<String>,
    blacklist: Vec<String>,
    whitelist_mode: bool,
) -> Vec<bool> {
    let wl: HashSet<String> = whitelist.into_iter().map(|m| m.to_uppercase()).collect();
    let bl: HashSet<String> = blacklist.into_iter().map(|m| m.to_uppercase()).collect();

    macs.iter().map(|mac| {
        let mac_upper = mac.to_uppercase();
        if bl.contains(&mac_upper) {
            return false;
        }
        if whitelist_mode && !wl.is_empty() && !wl.contains(&mac_upper) {
            return false;
        }
        true
    }).collect()
}


// ══════════════════════════════════════════════
//  BATCH PACKET PARSER
//  Parses raw CSV lines faster than Python split/int
// ══════════════════════════════════════════════

/// Parse a batch of raw CSV lines from the ESP32.
/// Expected format: TYPE,MAC,CHANNEL,RSSI
///
/// Returns:
///     Vec of (sig_type, mac, channel, rssi) tuples for valid lines.
///     Invalid lines are silently skipped.
#[pyfunction]
fn parse_packets(
    lines: Vec<String>,
    ch_min: i64,
    ch_max: i64,
    rssi_min: i64,
    rssi_max: i64,
) -> Vec<(String, String, i64, i64)> {
    let valid_types = ["ROUTER_BEACON", "DEVICE_PROBE", "DATA_FRAME"];
    let mut results = Vec::with_capacity(lines.len());

    for line in &lines {
        let parts: Vec<&str> = line.trim().splitn(4, ',').collect();
        if parts.len() != 4 {
            continue;
        }
        let sig_type = parts[0];
        if !valid_types.contains(&sig_type) {
            continue;
        }
        let mac = parts[1].to_uppercase();
        let channel: i64 = match parts[2].parse() {
            Ok(v) => v,
            Err(_) => continue,
        };
        let rssi: i64 = match parts[3].trim().parse() {
            Ok(v) => v,
            Err(_) => continue,
        };
        if channel < ch_min || channel > ch_max {
            continue;
        }
        if rssi < rssi_min || rssi > rssi_max {
            continue;
        }
        results.push((sig_type.to_string(), mac, channel, rssi));
    }

    results
}


// ══════════════════════════════════════════════
//  PYTHON MODULE REGISTRATION
// ══════════════════════════════════════════════

#[pymodule]
fn rf_core(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(compute_grid,    m)?)?;
    m.add_function(wrap_pyfunction!(filter_packets,  m)?)?;
    m.add_function(wrap_pyfunction!(parse_packets,   m)?)?;
    Ok(())
}