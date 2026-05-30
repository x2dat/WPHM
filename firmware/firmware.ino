#include "esp_wifi.h"
#include "nvs_flash.h"

#define CHANNEL_HOP_INTERVAL 150 
unsigned long lastChannelHop = 0;
uint8_t currentChannel = 1;

struct MacHeader {
    uint8_t frame_control[2];
    uint8_t duration[2];
    uint8_t addr1[6]; 
    uint8_t addr2[6]; // Source Address (Transmitting Device)
    uint8_t addr3[6]; 
    uint8_t seq_ctrl[2];
};

void passive_sniffer_cb(void* buf, wifi_promiscuous_pkt_type_t type) {
    wifi_promiscuous_pkt_t *pkt = (wifi_promiscuous_pkt_t *)buf;
    int rssi = pkt->rx_ctrl.rssi;
    int channel = pkt->rx_ctrl.channel;
    
    uint8_t *payload = pkt->payload;
    struct MacHeader* sniffer_header = (struct MacHeader*)payload;
    
    String signalType = "UNKNOWN";
    uint8_t frameType = payload[0] & 0xFC;
    
    if (frameType == 0x80) {
        signalType = "ROUTER_BEACON";
    } else if (frameType == 0x40) {
        signalType = "DEVICE_PROBE";
    } else if ((frameType & 0x0C) == 0x08) {
        signalType = "DATA_FRAME";
    }

    // Output clean CSV data over Serial to the laptop
    if (signalType == "UNKNOWN") return;
    Serial.print(signalType);
    Serial.print(",");
    
    for (int i = 0; i < 6; i++) {
        if (sniffer_header->addr2[i] < 0x10) Serial.print("0");
        Serial.print(sniffer_header->addr2[i], HEX);
        if (i < 5) Serial.print(":");
    }
    
    Serial.print(",");
    Serial.print(channel);
    Serial.print(",");
    Serial.println(rssi);
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("TYPE,MAC,CHANNEL,RSSI"); // CSV Data Header

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_NULL));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_rx_cb(&passive_sniffer_cb));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
    
    esp_wifi_set_channel(currentChannel, WIFI_SECOND_CHAN_NONE);
}

void loop() {
    if (millis() - lastChannelHop > CHANNEL_HOP_INTERVAL) {
        currentChannel++;
        if (currentChannel > 11) {
            currentChannel = 1;
        }
        esp_wifi_set_channel(currentChannel, WIFI_SECOND_CHAN_NONE);
        lastChannelHop = millis();
    }
}
