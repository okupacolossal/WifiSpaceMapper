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
#include "nvs_flash.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "secrets.h"

static const char *TAG = "wifi_csi";

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();                          // ready → start associating
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGI(TAG, "disconnected — retrying");
        esp_wifi_connect();                          // dropped → try again
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *) event_data;
        ESP_LOGI(TAG, "got ip: " IPSTR, IP2STR(&event->ip_info.ip));  // success!
    }
}

static void wifi_init_sta(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

        // Network plumbing: TCP/IP stack + the event system Wi-Fi reports through.
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    // Bring up the Wi-Fi driver with default settings.
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

        // Subscribe our handler to Wi-Fi events and the "got IP" event.
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                        &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                        &wifi_event_handler, NULL, NULL));

        // Tell the driver which network to join (from secrets.h).
    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));        // station (client) mode
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());   // powers on the radio
}

void app_main(void)
{
    ESP_LOGI(TAG, "WiFi Space Mapper booting — stage 0 skeleton alive");
    wifi_init_sta();
}
