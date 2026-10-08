idf_component_register(
    SRCS "pip_board.c" "pip_ui.c" "pip_assets.c" "inter_55.c" "inter_check.c" "inter_choice.c" "inter_confirm.c" "inter_arrows.c" "pip_network.c" "inter_20.c" "inter_28.c" "inter_40.c"
    INCLUDE_DIRS "include"
    REQUIRES esp32_s3_touch_amoled_1_8 esp_lvgl_port esp_lcd_touch lvgl esp_timer esp_psram esp_driver_usb_serial_jtag esp_wifi esp_event esp_netif nvs_flash
)
# ESP-IDF's Xtensa headers require GNU inline assembly extensions to C17.
target_compile_options(${COMPONENT_LIB} PRIVATE -std=gnu17)
target_compile_definitions(${COMPONENT_LIB} PRIVATE LV_LVGL_H_INCLUDE_SIMPLE)
