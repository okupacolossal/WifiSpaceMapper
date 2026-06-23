# Registers main as a component. Add libs to REQUIRES as each stage needs them:
#   esp_wifi  -> WiFi driver + CSI API     (stage 1-2)
#   nvs_flash -> WiFi needs NVS storage     (stage 1)
#   esp_netif esp_event -> network plumbing (stage 1)
#   lwip      -> ping the gateway           (stage 3)
idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES esp_wifi nvs_flash esp_netif esp_event lwip
)
