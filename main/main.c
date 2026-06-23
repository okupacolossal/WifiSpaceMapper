// WiFi Space Mapper — CSI capture firmware (built from scratch on ESP-IDF).
//
// Build-up plan (one stage at a time — prove each before the next):
//   Stage 0  [now]  Boot + print. Confirms the skeleton compiles & flashes.
//   Stage 1         WiFi station: init NVS, bring up esp_netif/esp_event,
//                   connect to your 2.4 GHz router, log "got IP".
//   Stage 2         CSI: on got-IP, esp_wifi_set_csi_config(),
//                   esp_wifi_set_csi_rx_cb(cb), esp_wifi_set_csi(true).
//                   In cb, printf the buffer as a CSV line over serial.
//   Stage 3         Self-ping the gateway ~10x/s so there's a steady stream
//                   of received frames to measure CSI on.
//   (host side)     Python: pyserial reads lines -> amplitude per subcarrier
//                   -> live matplotlib plot -> wave hand -> curves jump.

#include <stdio.h>
#include "esp_log.h"

static const char *TAG = "wifi_csi";

void app_main(void)
{
    ESP_LOGI(TAG, "WiFi Space Mapper booting — stage 0 skeleton alive");
    // Stage 1 starts here: your WiFi-connect code.
}
