#include "pip_board.h"
#include <stdatomic.h>
#include <string.h>
#include "esp_check.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "nvs_flash.h"

static const char *TAG = "pip_net";
static bool wifi_initialized;
static atomic_bool connected;
static atomic_bool connecting;
static atomic_bool started;
static atomic_uint retry_at;
static atomic_uint generation;

static void network_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    (void)data;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        atomic_store(&started, true);
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        atomic_store(&connected, false);
        atomic_store(&connecting, false);
        atomic_store(&retry_at, (uint32_t)(esp_timer_get_time() / 1000) + 5000);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        atomic_store(&connected, true);
        atomic_store(&connecting, false);
        ESP_LOGI(TAG, "Wi-Fi connected; host bridge available for connection");
    }
}

static esp_err_t connect_wifi(const char *ssid, const char *password)
{
    if (!wifi_initialized) {
        ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "network interface");
        ESP_RETURN_ON_ERROR(esp_event_loop_create_default(), TAG, "event loop");
        ESP_RETURN_ON_FALSE(esp_netif_create_default_wifi_sta(), ESP_ERR_NO_MEM, TAG, "station interface");
        wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
        ESP_RETURN_ON_ERROR(esp_wifi_init(&init), TAG, "Wi-Fi init");
        ESP_RETURN_ON_ERROR(esp_wifi_set_storage(WIFI_STORAGE_RAM), TAG, "Wi-Fi storage");
        ESP_RETURN_ON_ERROR(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, network_event, NULL), TAG, "Wi-Fi events");
        ESP_RETURN_ON_ERROR(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, network_event, NULL), TAG, "IP events");
        ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "station mode");
        wifi_initialized = true;
    } else {
        esp_wifi_disconnect();
    }
    wifi_config_t config = {0};
    memcpy(config.sta.ssid, ssid, strlen(ssid));
    memcpy(config.sta.password, password, strlen(password));
    config.sta.threshold.authmode = password[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &config), TAG, "station configuration");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "Wi-Fi start");
    ESP_RETURN_ON_ERROR(esp_wifi_set_ps(WIFI_PS_NONE), TAG, "Wi-Fi power mode");
    atomic_store(&connected, false);
    atomic_store(&connecting, false);
    atomic_store(&retry_at, 0);
    atomic_fetch_add(&generation, 1);
    return ESP_OK;
}

int32_t pip_network_load(pip_network_settings_t *settings)
{
    nvs_handle_t nvs;
    esp_err_t error = nvs_open("pip", NVS_READONLY, &nvs);
    if (error != ESP_OK) {
        return error;
    }
    size_t host_size = sizeof(settings->host), token_size = sizeof(settings->token);
    error = nvs_get_str(nvs, "host", settings->host, &host_size);
    if (error == ESP_OK) error = nvs_get_str(nvs, "token", settings->token, &token_size);
    if (error == ESP_OK) error = nvs_get_u16(nvs, "port", &settings->port);
    nvs_close(nvs);
    return error;
}

int32_t pip_network_init(void)
{
    ESP_RETURN_ON_ERROR(nvs_flash_init(), TAG, "NVS init");
    nvs_handle_t nvs;
    esp_err_t error = nvs_open("pip", NVS_READONLY, &nvs);
    if (error == ESP_ERR_NVS_NOT_FOUND) return ESP_OK;
    if (error != ESP_OK) return error;
    char ssid[33] = {0}, password[64] = {0};
    size_t ssid_size = sizeof(ssid), password_size = sizeof(password);
    error = nvs_get_str(nvs, "ssid", ssid, &ssid_size);
    if (error == ESP_OK) error = nvs_get_str(nvs, "password", password, &password_size);
    nvs_close(nvs);
    if (error == ESP_OK) error = connect_wifi(ssid, password);
    memset(password, 0, sizeof(password));
    return error;
}

int32_t pip_network_save(const char *ssid, const char *password, const pip_network_settings_t *settings)
{
    if (!ssid || !password || !settings || strlen(ssid) < 1 || strlen(ssid) > 32 ||
        strlen(password) > 63 || !settings->host[0] || strlen(settings->token) != 64 || !settings->port) {
        return ESP_ERR_INVALID_ARG;
    }
    nvs_handle_t nvs;
    esp_err_t error = nvs_open("pip", NVS_READWRITE, &nvs);
    if (error != ESP_OK) return error;
    error = nvs_set_str(nvs, "ssid", ssid);
    if (error == ESP_OK) error = nvs_set_str(nvs, "password", password);
    if (error == ESP_OK) error = nvs_set_str(nvs, "host", settings->host);
    if (error == ESP_OK) error = nvs_set_str(nvs, "token", settings->token);
    if (error == ESP_OK) error = nvs_set_u16(nvs, "port", settings->port);
    if (error == ESP_OK) error = nvs_commit(nvs);
    nvs_close(nvs);
    if (error == ESP_OK) error = connect_wifi(ssid, password);
    return error;
}

void pip_network_poll(void)
{
    uint32_t now = esp_timer_get_time() / 1000;
    if (atomic_load(&started) && !atomic_load(&connected) && !atomic_load(&connecting) &&
        (int32_t)(now - atomic_load(&retry_at)) >= 0) {
        atomic_store(&connecting, true);
        if (esp_wifi_connect() != ESP_OK) {
            atomic_store(&connecting, false);
            atomic_store(&retry_at, now + 5000);
        }
    }
}

int32_t pip_network_set_host(const char *host, uint16_t port)
{
    if (!host || !host[0] || strlen(host) >= 128 || !port) return ESP_ERR_INVALID_ARG;
    nvs_handle_t nvs;
    esp_err_t error = nvs_open("pip", NVS_READWRITE, &nvs);
    if (error != ESP_OK) return error;
    size_t size = 0;
    error = nvs_get_str(nvs, "ssid", NULL, &size);
    if (error == ESP_OK) error = nvs_set_str(nvs, "host", host);
    if (error == ESP_OK) error = nvs_set_u16(nvs, "port", port);
    if (error == ESP_OK) error = nvs_commit(nvs);
    nvs_close(nvs);
    if (error == ESP_OK) atomic_fetch_add(&generation, 1);
    return error;
}

int32_t pip_network_ready(void) { return atomic_load(&connected); }
uint32_t pip_network_generation(void) { return atomic_load(&generation); }
