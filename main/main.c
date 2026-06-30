// WiFi Space Mapper — CSI capture firmware (built from scratch on ESP-IDF).
//
// Pipeline:
//   1. Connect to the 2.4 GHz router as a Wi-Fi station (STA).
//   2. Enable Channel State Information (CSI) capture on the Wi-Fi driver.
//   3. Self-ping the gateway ~25x/s so the radio constantly RECEIVES frames.
//      CSI is only produced for *received* packets; a connected STA otherwise
//      only hears the router's beacons (~10x/s, irregular) — too sparse to run
//      a moving-variance motion detector on. Each ping reply is a frame that
//      travelled router -> board through the room's multipath, so it fires CSI.
//   4. In the CSI callback, print each frame as one CSV line over serial:
//          CSI_DATA,<len>,<rssi>,[b0 b1 b2 ...]
//      buf is interleaved signed (imag, real) byte pairs per subcarrier;
//      amplitude = sqrt(re^2 + im^2) is computed host-side.

#include <stdio.h>
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "ping/ping_sock.h"   // Stage 3 — self-ping to densify the CSI stream
#include "lwip/ip_addr.h"
#include "secrets.h"

static const char *TAG = "wifi_csi";

// Ping session handle — kept so we only ever start one, even if
// IP_EVENT_STA_GOT_IP fires again after a reconnect.
static esp_ping_handle_t s_ping = NULL;

// ---- Stage 3: steady stream of received frames ------------------------------
static void start_gateway_ping(const esp_ip4_addr_t *gw)
{
    if (s_ping) return;   // already running

    ip_addr_t target = {0};
    ip_addr_t *ptarget = &target;             // go through a pointer var so the
    ip_addr_set_ip4_u32(ptarget, gw->addr);   // macro's null-check doesn't trip
                                              // -Waddress; gateway IPv4 -> ip_addr_t

    esp_ping_config_t cfg = ESP_PING_DEFAULT_CONFIG();
    cfg.target_addr = target;
    cfg.count       = ESP_PING_COUNT_INFINITE;  // ping forever
    cfg.interval_ms = 10;                        // push the rate up — aim for ~100 replies/s

    esp_ping_callbacks_t cbs = {0};   // no per-ping callbacks — the CSI cb does the work
    if (esp_ping_new_session(&cfg, &cbs, &s_ping) == ESP_OK && esp_ping_start(s_ping) == ESP_OK) {
        ESP_LOGI(TAG, "gateway ping started (%d ms) — densifying CSI stream", (int) cfg.interval_ms);
    } else {
        ESP_LOGW(TAG, "failed to start gateway ping");
        s_ping = NULL;
    }
}

// ---- Stage 2: CSI callback (runs in the Wi-Fi task — keep it SHORT) ---------
static void csi_rx_callback(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf) return;
    // CSV line: marker, byte count, rssi, then the raw CSI bytes.
    printf("CSI_DATA,%u,%d,[", info->len, info->rx_ctrl.rssi);
    for (int i = 0; i < info->len; i++) {
        printf("%d ", info->buf[i]);
    }
    printf("]\n");
}

// ---- Wi-Fi / IP events ------------------------------------------------------
static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();                          // ready -> start associating
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *d = (wifi_event_sta_disconnected_t *) data;
        ESP_LOGW(TAG, "disconnected (reason %d, rssi %d) — retrying", d->reason, d->rssi);
        esp_wifi_connect();                          // dropped -> try again
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *) data;
        ESP_LOGI(TAG, "got ip: " IPSTR, IP2STR(&event->ip_info.ip));   // success!
        start_gateway_ping(&event->ip_info.gw);      // Stage 3: densify the RX stream
    }
}

// ---- Stage 1: bring up Wi-Fi (STA) + enable CSI -----------------------------
static void wifi_init_sta(void)
{
    esp_err_t ret = nvs_flash_init();   // Wi-Fi stores radio calibration in NVS
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
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));       // station (client) mode
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());                       // powers on the radio

    // Keep the radio fully awake (no modem-sleep). Two reasons:
    //  - far more robust on a marginal/weak link (no beacon-timeout drops), and
    //  - CSI needs the radio RECEIVING continuously; power-save would miss frames
    //    between beacons. Costs a little more current — fine on USB power.
    esp_wifi_set_ps(WIFI_PS_NONE);

    // Stage 2: enable CSI capture. (Needs CONFIG_ESP_WIFI_CSI_ENABLED=y.)
    // Capture only the Legacy LTF (64 subcarriers). Disabling the HT LTFs drops
    // the redundant 2nd/3rd channel measurements each packet carries: ~3x smaller
    // frames (64 vs 192 values) AND a uniform length for every received packet
    // (legacy beacons + HT ping replies alike) — both lift the achievable frame
    // rate and let the host drop its frame-type-locking logic.
    wifi_csi_config_t csi_config = {
        .lltf_en = true,          // Legacy LTF — present in every received packet
        .htltf_en = false,        // skip HT LTF (redundant for motion sensing)
        .stbc_htltf2_en = false,  // skip 2nd HT LTF (STBC)
        .ltf_merge_en = false,    // nothing to merge with a single LTF
        .channel_filter_en = true,
        .manu_scale = false,      // let the driver auto-scale the values
        .shift = 0,
        .dump_ack_en = false,
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(&csi_rx_callback, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

void app_main(void)
{
    ESP_LOGI(TAG, "WiFi Space Mapper booting");
    wifi_init_sta();
}
