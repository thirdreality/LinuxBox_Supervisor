/*
 *  BlueZ - Bluetooth protocol stack for Linux
 *
 *  Copyright (C) 2014  Google Inc.
 *
 *
 *  This program is free software; you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation; either version 2 of the License, or
 *  (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 * 
 *  # 2025-06-19: Update for wifi config
 *  # maintainer: guoping.liu@3reality.com
 */

#ifdef HAVE_CONFIG_H
#include <config.h>
#endif

#include <ctype.h>
#include <stdio.h>
#include <stdbool.h>
#include <stdint.h>
#include <time.h>
#include <stdlib.h>
#include <getopt.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <pthread.h>
#include <signal.h>
#include <sys/wait.h>
#include <sys/select.h>
#include <sys/signal.h>
#include <sys/types.h>

#include "lib/bluetooth.h"
#include "lib/hci.h"
#include "lib/hci_lib.h"
#include "lib/l2cap.h"
#include "lib/uuid.h"
#include "monitor/bt.h"


#include "src/shared/mainloop.h"
#include "src/shared/util.h"
#include "src/shared/att.h"
#include "src/shared/hci.h"
#include "src/shared/queue.h"
#include "src/shared/timeout.h"
#include "src/shared/gatt-db.h"
#include "src/shared/gatt-server.h"

#include <cjson/cJSON.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/ioctl.h>
#include <linux/types.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#include <linux/if_addr.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <syslog.h>

// Avoid multiple definitions of network interface flags
#ifdef __linux__
#include <net/if.h>
#endif

// Notification buffer
#define NOTIFY_BUFFER_SIZE 256
static uint8_t notify_buffer[NOTIFY_BUFFER_SIZE];
static bool notifying = false;
static bool notification_ready = false;
static pthread_mutex_t notification_lock = PTHREAD_MUTEX_INITIALIZER;
// Forward declarations
static void str2uuid(const char *str, uint8_t *value, uint8_t type);
static struct server *server_create(int fd);

static void start_advertising(void);
static void stop_advertising(void);
static void reset_no_client_timeout(void);
static void no_client_timeout_cb(int timeout_id, void *user_data);
static const char* get_device_name(void);
static int get_wifi_mac(char* mac_buf);
static void send_socket_command(const char *command);

// Keep the original 128-bit definitions for fallback
#define LINUXBOX_SERVICE_UUID_STR "6e400000-0000-4e98-8024-bc5b71e0893e"
#define WIFI_CONFIG_CHAR_UUID_STR "6e400001-0000-4e98-8024-bc5b71e0893e"


#define HUB_V3_SUPPORT

static struct hci_dev_info hdi;
static int ctl;
static struct server *server;

#define ATT_CID 4

// Timeout settings (5 minutes = 300 seconds)
#define NO_CLIENT_TIMEOUT_SECONDS 300

#define COLOR_OFF	"\x1B[0m"
#define COLOR_RED	"\x1B[0;91m"
#define COLOR_GREEN	"\x1B[0;92m"
#define COLOR_YELLOW	"\x1B[0;93m"
#define COLOR_BLUE	"\x1B[0;94m"
#define COLOR_MAGENTA	"\x1B[0;95m"
#define COLOR_BOLDGRAY	"\x1B[1;30m"
#define COLOR_BOLDWHITE	"\x1B[1;37m"


static bool verbose = false;
static int user_timeout_seconds = NO_CLIENT_TIMEOUT_SECONDS; // User-specified timeout

struct server {
	int fd;
	struct bt_att *att;
	struct gatt_db *db;
	struct bt_gatt_server *gatt;
	struct gatt_db_attribute *chara_att;
	uint16_t chara_handle;
	bool notifying;
	bool notification_ready;
	pthread_mutex_t notification_lock;
    // BLE GATT long write buffer for WiFi config
#define MAX_WRITE_BUFFER 1024
    char write_buffer[MAX_WRITE_BUFFER];
    size_t write_buffer_len;
    bool write_in_progress;
};

// Forward declaration
void ble_init(void);

// LED control macros
#define SUPERVISOR_PATH "/usr/local/bin/supervisor"
#define LED_SYS_WIFI_CONFIG_PENDING "led sys_wifi_config_pending"
#define LED_SYS_WIFI_CONFIGURING "led sys_wifi_configuring"
#define LED_SYS_WIFI_SUCCESS "led sys_wifi_config_success" 
#define LED_SYS_EVENT_OFF "led sys_wifi_config_stopped"

#define SETTING_WIFI_NOTIFY "setting wifi_notify"

// Global state management variables
static bool client_connected = false;
static bool advertising = false;
static int wifi_success_count = 0;
static volatile bool should_exit = false;
static unsigned int no_client_timeout_id = 0;



#define TEST_MAC_ADDRESS 0  // Set to 1 to enable test mode, 0 to disable
#define TEST_ATT_LOG 0  // Set to 1 to enable att log, 0 to disable
#define TEST_MAX_WIFI_SUCCESS_COUNT 1

static void send_socket_command(const char *command)
{
    char full_cmd[256];
    
    // Check if supervisor file exists
    if (access(SUPERVISOR_PATH, F_OK) != 0) {
        printf("[PROXY] Warning: %s not found, skipping LED command\n", SUPERVISOR_PATH);
        return;
    }
    
    snprintf(full_cmd, sizeof(full_cmd), "%s %s", SUPERVISOR_PATH, command);
    printf("[PROXY] Executing: %s\n", full_cmd);
    
    int result = system(full_cmd);
    if (result != 0) {
        printf("[PROXY] Warning: socket command failed: %d.\n", result);
    }
}

// WiFi management helper functions
static char* get_current_wifi_ssid(void)
{
    FILE *fp = popen("nmcli -t -f active,ssid dev wifi | grep '^yes:' | cut -d':' -f2", "r");
    if (!fp) {
        return NULL;
    }
    
    char *ssid = malloc(256);
    if (fgets(ssid, 256, fp)) {
        // Remove newline character
        ssid[strcspn(ssid, "\n")] = 0;
        pclose(fp);
        return ssid;
    }
    
    pclose(fp);
    free(ssid);
    return NULL;
}

static char* get_wlan_ip_address(void)
{
    FILE *fp = popen("ip -4 addr show wlan0 | grep -oP '(?<=inet\\s)\\d+(\\.\\d+){3}' | head -n1", "r");
    if (!fp) {
        return NULL;
    }
    
    char *ip = malloc(64);
    if (fgets(ip, 64, fp)) {
        // 移除换行符
        ip[strcspn(ip, "\n")] = 0;
        pclose(fp);
        return ip;
    }
    
    pclose(fp);
    free(ip);
    return NULL;
}

static bool is_valid_ip(const char *ip)
{
    if (!ip || strlen(ip) == 0) {
        return false;
    }
    
    // 检查是否为有效的IPv4地址（简单检查）
    int parts = 0;
    char *ip_copy = strdup(ip);
    char *token = strtok(ip_copy, ".");
    
    while (token != NULL && parts < 4) {
        int num = atoi(token);
        if (num < 0 || num > 255) {
            free(ip_copy);
            return false;
        }
        parts++;
        token = strtok(NULL, ".");
    }
    
    free(ip_copy);
    return parts == 4;
}

static void cleanup_old_connections(const char *current_ssid)
{
    char cmd[512];
    FILE *fp;
    char line[256];
    
    printf("[WIFI] Cleaning up old WiFi connections (keeping: %s)\n", current_ssid);
    
    // 获取所有WiFi连接
    fp = popen("nmcli -t -f name,type connection show | grep ':802-11-wireless' | cut -d':' -f1", "r");
    if (!fp) {
        printf("[WIFI] Failed to get connection list\n");
        return;
    }
    
    while (fgets(line, sizeof(line), fp)) {
        // 移除换行符
        line[strcspn(line, "\n")] = 0;
        
        // 跳过当前SSID的连接
        if (strcmp(line, current_ssid) == 0) {
            continue;
        }
        
        // 删除其他WiFi连接
        snprintf(cmd, sizeof(cmd), "nmcli connection delete '%s'", line);
        printf("[WIFI] Removing old connection: %s\n", line);
        system(cmd);
    }
    
    pclose(fp);
}

static int process_wifi_config(const char *json_str, char *response, size_t response_len)
{
    printf("[DEBUG] Processing JSON: %s\n", json_str);
    
    // 发送WiFi配置中LED指令
    send_socket_command(LED_SYS_WIFI_CONFIGURING);
    
    cJSON *root = cJSON_Parse(json_str);
    if (!root) {
        printf("[DEBUG] Failed to parse JSON\n");
        snprintf(response, response_len, "{\"err\":\"bad fmt\"}");
        return -1;
    }

    cJSON *ssid_item = cJSON_GetObjectItem(root, "ssid");
    if (!ssid_item || !cJSON_IsString(ssid_item)) {
        printf("[DEBUG] Missing or invalid SSID\n");
        cJSON_Delete(root);
        snprintf(response, response_len, "{\"err\":\"bad ssid\"}");
        return -1;
    }

    char *ssid = ssid_item->valuestring;
    cJSON *password_item = cJSON_GetObjectItem(root, "pw");
    char *password = NULL;
    if (password_item && cJSON_IsString(password_item)) {
        password = password_item->valuestring;
    }

    printf("[DEBUG] Target SSID: %s, Password: %s\n", 
           ssid, password ? "***" : "none");

    // 1. 检查当前连接的SSID是否与目标SSID一致
    char *current_ssid = get_current_wifi_ssid();
    if (current_ssid && strcmp(current_ssid, ssid) == 0) {
        printf("[WIFI] Already connected to target SSID: %s\n", ssid);
        
        // 获取当前IP地址
        char *current_ip = get_wlan_ip_address();
        if (current_ip && is_valid_ip(current_ip)) {
            printf("[WIFI] Current connection is valid with IP: %s\n", current_ip);
            wifi_success_count++;
            
            // 发送成功LED指令
            send_socket_command(LED_SYS_WIFI_SUCCESS);
            
            snprintf(response, response_len, "{\"ip\":\"%s\"}", current_ip);
            
            free(current_ssid);
            free(current_ip);
            cJSON_Delete(root);
            return 0;
        }
        
        if (current_ip) free(current_ip);
    }
    
    if (current_ssid) free(current_ssid);

    // 2. 使用nmcli连接新的WiFi网络
    char connect_cmd[512];
    if (password && strlen(password) > 0) {
        snprintf(connect_cmd, sizeof(connect_cmd), 
                "nmcli device wifi connect '%s' password '%s' 2>&1", ssid, password);
    } else {
        snprintf(connect_cmd, sizeof(connect_cmd), 
                "nmcli device wifi connect '%s' 2>&1", ssid);
    }
    
    printf("[WIFI] Connecting with command: nmcli device wifi connect '%s' password '%s'\n", 
           ssid, password ? "***" : "none");
    
    // 执行连接命令
    FILE *cmd_fp = popen(connect_cmd, "r");
    if (!cmd_fp) {
        printf("[WIFI] Failed to execute nmcli command\n");
        snprintf(response, response_len, "{\"err\":\"cmd fail\"}");
        cJSON_Delete(root);
        return -1;
    }
    
    // Device 'wlan0' successfully activated with '26229f20-a62f-4192-9858-70e241bd141d'.
    // Error: Connection activation failed: Secrets were required, but not provided.
    // Error: No network with SSID 'TPLINK-20202' found.
    char cmd_output[512] = {0};
    bool cmd_success = false;
    bool need_scan_retry = false;
    
    if (fgets(cmd_output, sizeof(cmd_output), cmd_fp)) {
        printf("[WIFI] nmcli output: %s", cmd_output);
        
        // Simple check: if output contains "successfully activated", command succeeded
        if (strstr(cmd_output, "successfully activated") != NULL) {
            cmd_success = true;
            printf("[WIFI] nmcli command executed successfully\n");
        } else {
            printf("[WIFI] nmcli command failed\n");
            // Check if error is "No network with SSID ... found"
            if (strstr(cmd_output, "No network with SSID") != NULL) {
                need_scan_retry = true;
                printf("[WIFI] Network not found in cache, will try scanning\n");
            }
        }
    } else {
        printf("[WIFI] No output from nmcli command\n");
    }
    
    int cmd_exit_status = pclose(cmd_fp);
    printf("[WIFI] nmcli exit status: %d\n", cmd_exit_status);
    
    // If command failed but it's a "network not found" error, try scanning and reconnecting
    if (!cmd_success && need_scan_retry) {
        printf("[WIFI] Scanning for WiFi networks before retry...\n");
        
        // Perform WiFi scan
        char scan_cmd[] = "nmcli dev wifi list ifname wlan0";
        FILE *scan_fp = popen(scan_cmd, "r");
        if (scan_fp) {
            char scan_line[256];
            printf("[WIFI] Scan results:\n");
            while (fgets(scan_line, sizeof(scan_line), scan_fp)) {
                printf("[WIFI] %s", scan_line);
            }
            pclose(scan_fp);
        } else {
            printf("[WIFI] Failed to perform WiFi scan\n");
        }
        
        // Wait a moment for scan to complete
        sleep(1);
        
        // Retry the connection command
        printf("[WIFI] Retrying connection after scan...\n");
        cmd_fp = popen(connect_cmd, "r");
        if (!cmd_fp) {
            printf("[WIFI] Failed to execute retry nmcli command\n");
            snprintf(response, response_len, "{\"err\":\"cmd fail\"}");
            cJSON_Delete(root);
            return -1;
        }
        
        // Check retry result
        cmd_success = false;
        memset(cmd_output, 0, sizeof(cmd_output));
        
        if (fgets(cmd_output, sizeof(cmd_output), cmd_fp)) {
            printf("[WIFI] nmcli retry output: %s", cmd_output);
            
            if (strstr(cmd_output, "successfully activated") != NULL) {
                cmd_success = true;
                printf("[WIFI] nmcli retry command executed successfully\n");
            } else {
                printf("[WIFI] nmcli retry command failed\n");
            }
        } else {
            printf("[WIFI] No output from nmcli retry command\n");
        }
        
        cmd_exit_status = pclose(cmd_fp);
        printf("[WIFI] nmcli retry exit status: %d\n", cmd_exit_status);
    }
    
    // If command failed after retry (or no retry was needed), return error
    if (!cmd_success || cmd_exit_status != 0) {
        printf("[WIFI] nmcli command failed, not checking IP address\n");
        snprintf(response, response_len, "{\"err\":\"conn fail\"}");
        cJSON_Delete(root);
        return -1;
    }
    
    // 3. 在1秒内检查IP地址 (极大减少阻塞时间防止BLE超时)
    printf("[WIFI] nmcli command successful, waiting up to 1 second for IP address...\n");
    for (int i = 0; i < 1; i++) {
        sleep(1);
        
        // 检查BLE连接状态，如果已断开则立即退出
        if (!client_connected) {
            printf("[WIFI] BLE client disconnected during WiFi config, aborting\n");
            snprintf(response, response_len, "{\"err\":\"BLE lost\"}");
            cJSON_Delete(root);
            return -1;
        }
        
        char *ip = get_wlan_ip_address();
        if (ip && is_valid_ip(ip)) {
            printf("[WIFI] WiFi connection successful! IP: %s (after %d seconds)\n", ip, i + 1);
            wifi_success_count++;
            
            // 发送成功LED指令
            send_socket_command(LED_SYS_WIFI_SUCCESS);
            
            // 4. 清理旧的连接
            cleanup_old_connections(ssid);
            
            snprintf(response, response_len, "{\"ip\":\"%s\"}", ip);
            
            free(ip);
            cJSON_Delete(root);
            return 0;
        }
        
        if (ip) free(ip);
        
        // 打印进度  
        printf("[WIFI] Still waiting for IP address... (%d/1 seconds)\n", i + 1);
    }
    
    // 1秒后仍未获得有效IP地址
    printf("[WIFI] WiFi connection failed - no valid IP address after 1 second\n");
    
    // 发送配置中LED指令（表示等待重试）
    send_socket_command(LED_SYS_WIFI_CONFIG_PENDING);
    
    // 检查nmcli连接状态
    char status_cmd[256];
    snprintf(status_cmd, sizeof(status_cmd), "nmcli connection show '%s' 2>/dev/null", ssid);
    FILE *status_fp = popen(status_cmd, "r");
    
    char error_detail[256] = "Connection timeout";
    if (status_fp) {
        char status_line[256];
        if (fgets(status_line, sizeof(status_line), status_fp)) {
            if (strstr(status_line, "GENERAL.STATE")) {
                snprintf(error_detail, sizeof(error_detail), "Connection created but not activated");
            }
        }
        pclose(status_fp);
    }
    
    snprintf(response, response_len, "{\"ip\":\"\"}");
    
    cJSON_Delete(root);
    return -1;
}


/*******************config wifi zone end*********************************************/
static struct bt_hci *hci_dev;



static void att_connect_cb(bool success, uint8_t att_ecode, void *user_data)
{
    struct server *server = user_data;
    printf("ATT Connect callback: success=%d, att_ecode=%d\n", success, att_ecode);
    
    if (success) {
        printf("[CONNECT] Client connected successfully\n");
        
        // Update connection status
        client_connected = true;
        
        // Stop advertising
        stop_advertising();
        
        // Stop no-client timeout timer
        if (no_client_timeout_id > 0) {
            mainloop_remove_timeout(no_client_timeout_id);
            no_client_timeout_id = 0;
            printf("[TIMEOUT] Stopped no client timeout due to connection\n");
        }
        
        printf("[CONNECT] Connection established, advertising stopped\n");
        printf("[CONNECT] Note: Using optimized connection parameters to prevent timeouts\n");
    } else {
        printf("[CONNECT] Connection failed with ATT error code: %d\n", att_ecode);
        if (att_ecode == 8) {
            printf("[CONNECT] Error 8 = Connection timeout - consider signal strength and distance\n");
        }
    }
}

static void att_disconnect_cb(int err, void *user_data)
{
    struct server *server = user_data;
    printf("ATT Disconnect callback: err=%d (%s)\n", err, strerror(err));
    
    printf("[DISCONNECT] Client disconnected\n");
    
    // CRITICAL: Update connection status immediately
    client_connected = false;
    
    // Special handling for error 8 (LINK_SUPERVISION_TIMEOUT)
    if (err == 8) {
        printf("[DISCONNECT] LINK_SUPERVISION_TIMEOUT detected - connection lost due to timeout\n");
    }
    
    // Check if should exit
    if (wifi_success_count > TEST_MAX_WIFI_SUCCESS_COUNT) {
        printf("[EXIT] WiFi configured %d times, exiting after disconnect\n", 
               wifi_success_count);
        send_socket_command(LED_SYS_EVENT_OFF);
        should_exit = true;
        mainloop_quit();
        return;
    }
    
    printf("[DISCONNECT] Will restart listening for new connections\n");
    
    // Trigger mainloop exit to let main function restart listening
    mainloop_quit();
}

static void att_debug_cb(const char *str, void *user_data)
{
#if TEST_ATT_LOG    
    printf("ATT Debug: %s\n", str);
#endif
}

static void gatt_debug_cb(const char *str, void *user_data)
{
#if TEST_ATT_LOG    
    printf("GATT Debug: %s\n", str);
#endif
}

static void user_service_read_cb(struct gatt_db_attribute *attrib,
                unsigned int id, uint16_t offset, uint8_t opcode,
                struct bt_att *att, void *user_data)
{
    struct server *server = user_data;
    uint8_t error = 0;
    const uint8_t *value = NULL;
    size_t len = 0;

    printf("Read request received - handle: 0x%04x, offset: %d\n",
           gatt_db_attribute_get_handle(attrib), offset);

    if (offset > 0) {
        printf("Read with offset not supported\n");
        error = BT_ATT_ERROR_INVALID_OFFSET;
        goto done;
    }

    // For read operations, we don't have any stored value to return
    // This characteristic is primarily for write operations
    error = BT_ATT_ERROR_READ_NOT_PERMITTED;

done:
    gatt_db_attribute_read_result(attrib, id, error, value, len);
}

static void user_service_write_cb(struct gatt_db_attribute *attrib,
                unsigned int id, uint16_t offset, const uint8_t *value,
                size_t len, uint8_t opcode, struct bt_att *att,
                void *user_data)
{
    struct server *server = user_data;
    char *str;

    printf("Write request received - handle: 0x%04x, offset: %d, len: %zu\n",
           gatt_db_attribute_get_handle(attrib), offset, len);

    str = strndup((char *) value, len);
    printf("Write value: %s\n", str);
    free(str);

    if (offset > 0) {
        printf("Write with offset not supported\n");
        gatt_db_attribute_write_result(attrib, id, BT_ATT_ERROR_INVALID_OFFSET);
        return;
    }

    if (len > 512) {
        printf("Write value too long: %zu > 512\n", len);
        gatt_db_attribute_write_result(attrib, id, BT_ATT_ERROR_INVALID_ATTRIBUTE_VALUE_LEN);
        return;
    }

    gatt_db_attribute_write_result(attrib, id, 0);
}

static void user_service_notify_cb(struct gatt_db_attribute *attrib,
                unsigned int id, uint16_t offset, const uint8_t *value,
                size_t len, uint8_t opcode, struct bt_att *att,
                void *user_data)
{
    struct server *server = user_data;
    char *str;

    printf("Notification request received - handle: 0x%04x, offset: %d, len: %zu\n",
           gatt_db_attribute_get_handle(attrib), offset, len);

    str = strndup((char *) value, len);
    printf("Notification value: %s\n", str);
    free(str);

    gatt_db_attribute_write_result(attrib, id, 0);
}

static void user_service_indicate_cb(struct gatt_db_attribute *attrib,
                unsigned int id, uint16_t offset, const uint8_t *value,
                size_t len, uint8_t opcode, struct bt_att *att,
                void *user_data)
{
    struct server *server = user_data;
    char *str;

    printf("Indication request received - handle: 0x%04x, offset: %d, len: %zu\n",
           gatt_db_attribute_get_handle(attrib), offset, len);

    str = strndup((char *) value, len);
    printf("Indication value: %s\n", str);
    free(str);

    gatt_db_attribute_write_result(attrib, id, 0);
}

static void wifi_config_read_cb(struct gatt_db_attribute *attrib,
		unsigned int id, uint16_t offset,
		uint8_t opcode, struct bt_att *att,
		void *user_data)
{
	struct server *server = user_data;
	uint8_t value[1] = { 0x00 };

	printf("[DEBUG] wifi_config_read_cb called\n");
	printf("[DEBUG] - id: %u\n", id);
	printf("[DEBUG] - offset: %u\n", offset);
	printf("[DEBUG] - opcode: 0x%02x\n", opcode);

	if (!server->notifying) {
		printf("[DEBUG] - server not notifying, sending error\n");
		gatt_db_attribute_read_result(attrib, id, BT_ATT_ERROR_REQUEST_NOT_SUPPORTED, NULL, 0);
		return;
	}

	pthread_mutex_lock(&server->notification_lock);
	if (server->notification_ready) {
		printf("[DEBUG] - notification ready, sending error\n");
		gatt_db_attribute_read_result(attrib, id, BT_ATT_ERROR_ATTRIBUTE_NOT_LONG, NULL, 0);
	} else {
		printf("[DEBUG] - waiting for notification\n");
		// Wait for notification to be ready
		server->notification_ready = true;
		gatt_db_attribute_read_result(attrib, id, BT_ATT_ERROR_ATTRIBUTE_NOT_LONG, NULL, 0);
	}
	pthread_mutex_unlock(&server->notification_lock);
}

static void send_notification(struct server *server, const char *message)
{
    if (!server->chara_att) {
        printf("[DEBUG] No characteristic attribute available for notification\n");
        return;
    }

    if (!server->gatt) {
        printf("[DEBUG] No GATT server available for notification\n");
        return;
    }

    size_t message_len = strlen(message);
    // 判断是否需要分片：如果消息长度<=20字节，强制单包
    if (message_len <= 20) {
        uint8_t buffer[21];
        memcpy(buffer, message, message_len);
        // 不加换行符，严格按表格
        bool result = bt_gatt_server_send_notification(server->gatt, server->chara_handle,
                buffer, message_len, false);
        printf("[DEBUG] Single packet notification result: %s\n", result ? "SUCCESS" : "FAILED");
        return;
    }
    // 超过20字节，按原有分片逻辑
    // ... existing code ...

    // Add '\n' terminator to the message for protocol compliance
    size_t total_len = message_len + 1; // +1 for '\n' terminator
    
    // Get current MTU and calculate max payload for notifications
    // Notification format: opcode (1 byte) + handle (2 bytes) + data
    uint16_t current_mtu = bt_gatt_server_get_mtu(server->gatt);
    size_t max_payload = current_mtu - 3;  // MTU - (opcode + handle)
    
    printf("[DEBUG] Sending notification: %s (length: %zu, total with newline: %zu)\n", 
           message, message_len, total_len);
    printf("[DEBUG] Current MTU: %u, Max payload: %zu\n", current_mtu, max_payload);
    
    if (total_len <= max_payload) {
        // Single packet - create buffer with terminator
        uint8_t *buffer = malloc(total_len);
        if (!buffer) {
            printf("[DEBUG] Failed to allocate buffer for notification\n");
            return;
        }
        
        memcpy(buffer, message, message_len);
        buffer[message_len] = '\n'; // Add newline terminator
        
        bool result = bt_gatt_server_send_notification(server->gatt, server->chara_handle,
                buffer, total_len, false);
        printf("[DEBUG] Single packet notification result: %s\n", result ? "SUCCESS" : "FAILED");
        
        free(buffer);
        return;
    }
    
    // Multi-packet: need fragmentation for MTU=23
    printf("[DEBUG] Message too long (%zu bytes with newline), fragmenting into %zu-byte chunks\n", 
           total_len, max_payload);
    
    size_t offset = 0;
    int fragment_num = 0;
    
    while (offset < total_len) {
        size_t remaining = total_len - offset;
        size_t chunk_size = (remaining > max_payload) ? max_payload : remaining;
        
        printf("[DEBUG] Sending fragment %d: offset=%zu, size=%zu\n", 
               fragment_num, offset, chunk_size);
        
        // Create buffer for this fragment
        uint8_t *buffer = malloc(chunk_size);
        if (!buffer) {
            printf("[DEBUG] Failed to allocate buffer for fragment %d\n", fragment_num);
            break;
        }
        
        // Copy data for this fragment
        if (offset < message_len) {
            // Copy message data
            size_t data_to_copy = (chunk_size <= (message_len - offset)) ? chunk_size : (message_len - offset);
            memcpy(buffer, message + offset, data_to_copy);
            
            // If this fragment includes the end of message, add terminator
            if (offset + data_to_copy >= message_len) {
                buffer[data_to_copy] = '\n';
            }
        } else {
            // This fragment only contains the terminator
            buffer[0] = '\n';
        }
        
        bool result = bt_gatt_server_send_notification(server->gatt, server->chara_handle,
                buffer, chunk_size, false);
        
        free(buffer);
        
        if (!result) {
            printf("[DEBUG] Fragment %d failed to send\n", fragment_num);
            break;
        }
        
        printf("[DEBUG] Fragment %d sent successfully\n", fragment_num);
        
        offset += chunk_size;
        fragment_num++;
        
        // Add delay between fragments to prevent overwhelming the client
        if (offset < total_len) {
            usleep(50000); // 50ms delay for MTU=23 to ensure stable delivery
        }
    }
    
    printf("[DEBUG] Fragmentation complete: sent %d fragments, total %zu bytes\n", 
           fragment_num, offset);
}

static void wifi_config_write_cb(struct gatt_db_attribute *attrib,
                unsigned int id, uint16_t offset,
                const uint8_t *value, size_t len,
                uint8_t opcode, struct bt_att *att,
                void *user_data)
{
    struct server *server = user_data;
    char *json_str = NULL;
    char response[256];
    int ret;

    // Respond to write immediately to prevent timeout
    gatt_db_attribute_write_result(attrib, id, 0);

    // Handle Prepare Write (0x16), Execute Write (0x18), Write Request (0x12)
    if (opcode == BT_ATT_OP_PREP_WRITE_REQ) {
        // 分包写入，缓存数据
        printf("[DEBUG] Prepare Write: offset=%u, len=%zu\n", offset, len);
        if (len > 0) {
            printf("[DEBUG] Prepare Write value (hex):");
            for (size_t i = 0; i < len; i++) {
                printf(" %02x", value[i]);
            }
            printf("\n");
            memcpy(server->write_buffer + offset, value, len);
            if (offset + len > server->write_buffer_len)
                server->write_buffer_len = offset + len;
            printf("[DEBUG] After memcpy, write_buffer_len=%zu\n", server->write_buffer_len);
            printf("[DEBUG] Current write_buffer (hex):");
            for (size_t i = 0; i < server->write_buffer_len; i++) {
                printf(" %02x", (unsigned char)server->write_buffer[i]);
            }
            printf("\n");
        } else {
            printf("[DEBUG] Prepare Write: len=0, skip memcpy\n");
        }
        server->write_in_progress = true;
        return; // 等待 Execute Write
    } else if (opcode == BT_ATT_OP_EXEC_WRITE_REQ) {
        // 分包写入完成，处理缓存
        printf("[DEBUG] Execute Write: buffer_len=%zu\n", server->write_buffer_len);
        printf("[DEBUG] Execute Write: buffer (hex):");
        for (size_t i = 0; i < server->write_buffer_len; i++) {
            printf(" %02x", (unsigned char)server->write_buffer[i]);
        }
        printf("\n");
        if (!server->write_in_progress || server->write_buffer_len == 0) {
            printf("[DEBUG] Execute Write but no data in buffer\n");
            snprintf(response, sizeof(response), "{\"ip\":\"\"}");
            goto send_response;
        }
        // 查找换行符，截断
        size_t actual_len = server->write_buffer_len;
        for (size_t i = 0; i < server->write_buffer_len; i++) {
            if (server->write_buffer[i] == '\n') {
                actual_len = i;
                break;
            }
        }
        json_str = malloc(actual_len + 1);
        if (!json_str) {
            snprintf(response, sizeof(response), "{\"ip\":\"\"}");
            server->write_buffer_len = 0;
            server->write_in_progress = false;
            goto send_response;
        }
        memcpy(json_str, server->write_buffer, actual_len);
        json_str[actual_len] = '\0';
        printf("[DEBUG] Execute Write: JSON: '%s'\n", json_str);
        server->write_buffer_len = 0;
        server->write_in_progress = false;
        // 处理 WiFi 配置
        if (!client_connected) {
            snprintf(response, sizeof(response), "{\"err\":\"BLE lost\"}");
            free(json_str);
            goto send_response;
        }
        ret = process_wifi_config(json_str, response, sizeof(response));
        free(json_str);
        goto send_response;
    } else if (opcode == BT_ATT_OP_WRITE_REQ) {
        // 直接写入
        printf("[DEBUG] Direct Write: offset=%u, len=%zu\n", offset, len);
        if (len > 0) {
            printf("[DEBUG] Direct Write value (hex):");
            for (size_t i = 0; i < len; i++) {
                printf(" %02x", value[i]);
            }
            printf("\n");
        }
        if (offset > 0) {
            printf("[DEBUG] Write request with offset not supported for WiFi config\n");
            snprintf(response, sizeof(response), "{\"ip\":\"\"}");
            goto send_response;
        }
        if (len == 0) {
            snprintf(response, sizeof(response), "{\"ip\":\"\"}");
            goto send_response;
        }
        // 查找换行符，截断
        size_t actual_len = len;
        for (size_t i = 0; i < len; i++) {
            if (value[i] == '\n') {
                actual_len = i;
                break;
            }
        }
        json_str = malloc(actual_len + 1);
        if (!json_str) {
            snprintf(response, sizeof(response), "{\"ip\":\"\"}");
            goto send_response;
        }
        memcpy(json_str, value, actual_len);
        json_str[actual_len] = '\0';
        printf("[DEBUG] Direct Write: JSON: '%s'\n", json_str);
        if (!client_connected) {
            snprintf(response, sizeof(response), "{\"err\":\"BLE lost\"}");
            free(json_str);
            goto send_response;
        }
        ret = process_wifi_config(json_str, response, sizeof(response));
        free(json_str);
        goto send_response;
    } else if (opcode == BT_ATT_OP_WRITE_CMD) {
        // Write Without Response 分片缓存处理，兼容 iOS 长数据
        printf("[DEBUG] Write Without Response (opcode=0x52): offset=%u, len=%zu\n", offset, len);
        if (len > 0) {
            printf("[DEBUG] Write Without Response value (hex):");
            for (size_t i = 0; i < len; i++) {
                printf(" %02x", value[i]);
            }
            printf("\n");
        }
        if (offset > 0) {
            printf("[DEBUG] Write Without Response with offset not supported for WiFi config\n");
            return;
        }
        if (len == 0) {
            return;
        }
        // 追加到缓存
        if (server->write_buffer_len + len > MAX_WRITE_BUFFER) {
            printf("[DEBUG] Write buffer overflow: %zu + %zu > %d\n", server->write_buffer_len, len, MAX_WRITE_BUFFER);
            server->write_buffer_len = 0;
            return;
        }
        memcpy(server->write_buffer + server->write_buffer_len, value, len);
        server->write_buffer_len += len;
        printf("[DEBUG] After append, write_buffer_len=%zu\n", server->write_buffer_len);
        printf("[DEBUG] Current write_buffer (hex):");
        for (size_t i = 0; i < server->write_buffer_len; i++) {
            printf(" %02x", (unsigned char)server->write_buffer[i]);
        }
        printf("\n");
        // 检查是否有换行符
        size_t json_end = 0;
        bool found_newline = false;
        for (size_t i = 0; i < server->write_buffer_len; i++) {
            if (server->write_buffer[i] == '\n') {
                json_end = i;
                found_newline = true;
                break;
            }
        }
        if (!found_newline) {
            printf("[DEBUG] No newline found, waiting for more fragments\n");
            return;
        }
        // 处理完整 JSON
        char *json_str = malloc(json_end + 1);
        if (!json_str) {
            server->write_buffer_len = 0;
            return;
        }
        memcpy(json_str, server->write_buffer, json_end);
        json_str[json_end] = '\0';
        printf("[DEBUG] Write Without Response: JSON: '%s'\n", json_str);
        // 清空缓存
        server->write_buffer_len = 0;
        if (!client_connected) {
            free(json_str);
            return;
        }
        int ret = process_wifi_config(json_str, response, sizeof(response));
        free(json_str);
        // 发送通知
        pthread_mutex_lock(&server->notification_lock);
        if (server->notifying && client_connected) {
            printf("[DEBUG] Sending WiFi result notification: %s\n", response);
            send_notification(server, response);
        } else {
            printf("[DEBUG] Client not subscribed to notifications or disconnected, cannot send result\n");
        }
        pthread_mutex_unlock(&server->notification_lock);
        printf("[DEBUG] ================== WIFI CONFIG COMPLETE ==================\n");
        return;
    } else {
        printf("[DEBUG] Unsupported opcode: 0x%02x\n", opcode);
        snprintf(response, sizeof(response), "{\"ip\":\"\"}");
        goto send_response;
    }

send_response:
    if (!client_connected) {
        printf("[DEBUG] BLE client disconnected, cannot send notification\n");
        return;
    }
    usleep(100000);
    if (!client_connected) {
        printf("[DEBUG] BLE client disconnected during delay, cannot send notification\n");
        return;
    }
    pthread_mutex_lock(&server->notification_lock);
    if (server->notifying && client_connected) {
        printf("[DEBUG] Sending WiFi result notification: %s\n", response);
        send_notification(server, response);
    } else {
        printf("[DEBUG] Client not subscribed to notifications or disconnected, cannot send result\n");
    }
    pthread_mutex_unlock(&server->notification_lock);
    printf("[DEBUG] ================== WIFI CONFIG COMPLETE ==================\n");
}

static void str2uuid(const char *str, uint8_t *value, uint8_t type)
{
    // Parse UUID: 6e400000-0000-4e98-8024-bc5b71e0893e
    // Should result in the same UUID when displayed in apps
    
    if (strlen(str) != 36) {
        printf("[ERROR] Invalid UUID length: %zu, expected 36\n", strlen(str));
        return;
    }
    
    // Simple hex string parsing, treating UUID as byte array
    int i = 0, j = 0;
    char buf[3] = {0};
    
    while (i < 36 && j < 16) {
        if (str[i] == '-') {
            i++;
            continue;
        }
        buf[0] = str[i++];
        buf[1] = str[i++];
        value[j++] = (uint8_t)strtoul(buf, NULL, 16);
    }
    
    printf("[DEBUG] UUID string: %s\n", str);
    printf("[DEBUG] UUID bytes: ");
    for (int k = 0; k < 16; k++) {
        printf("%02x ", value[k]);
    }
    printf("\n");
    
    // Verify: reconstruct UUID string to check
    char reconstructed[40];
    snprintf(reconstructed, sizeof(reconstructed),
        "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        value[0], value[1], value[2], value[3],
        value[4], value[5], value[6], value[7],
        value[8], value[9], value[10], value[11], 
        value[12], value[13], value[14], value[15]);
    printf("[DEBUG] Reconstructed: %s\n", reconstructed);
}

static void cccd_read_cb(struct gatt_db_attribute *attrib,
				unsigned int id, uint16_t offset,
				uint8_t opcode, struct bt_att *att,
				void *user_data)
{
	struct server *server = user_data;
	uint8_t value[2] = {0, 0};

	printf("[DEBUG] cccd_read_cb called\n");
	
	if (server->notifying) {
		value[0] = 0x01; // Notifications enabled
	}

	gatt_db_attribute_read_result(attrib, id, 0, value, 2);
}

static void cccd_write_cb(struct gatt_db_attribute *attrib,
				unsigned int id, uint16_t offset,
				const uint8_t *value, size_t len,
				uint8_t opcode, struct bt_att *att,
				void *user_data)
{
	struct server *server = user_data;

	printf("[DEBUG] cccd_write_cb called, len: %zu\n", len);
	
	if (len != 2) {
		printf("[DEBUG] Invalid CCCD value length: %zu\n", len);
		gatt_db_attribute_write_result(attrib, id, BT_ATT_ERROR_INVALID_ATTRIBUTE_VALUE_LEN);
		return;
	}

	uint16_t cccd_value = (value[1] << 8) | value[0];  // Little endian
	printf("[DEBUG] CCCD value: 0x%04x (little endian: 0x%02x%02x)\n", cccd_value, value[0], value[1]);

	pthread_mutex_lock(&server->notification_lock);
	
	if (cccd_value & 0x01) {
		printf("[DEBUG] Notifications enabled by client (0x01 bit set)\n");
		server->notifying = true;
		server->notification_ready = true;
	} else if (cccd_value & 0x02) {
		printf("[DEBUG] Indications enabled by client (0x02 bit set)\n");
		server->notifying = true;  // We'll treat indications as notifications
		server->notification_ready = true;
	} else {
		printf("[DEBUG] Notifications and indications disabled by client\n");
		server->notifying = false;
		server->notification_ready = false;
	}
	
	pthread_mutex_unlock(&server->notification_lock);
	
	gatt_db_attribute_write_result(attrib, id, 0);
	
	// If this is our WiFi service being enabled, send test notification
	uint16_t handle = gatt_db_attribute_get_handle(attrib);
	printf("[DEBUG] CCCD write on handle: 0x%04x, WiFi char handle: 0x%04x\n", 
	       handle, server->chara_handle);
	
	if (server->notifying && (handle == (server->chara_handle + 1))) {
		// CCCD is typically handle + 1 from characteristic
		printf("[DEBUG] WiFi service notifications enabled! Sending test notification\n");
		//send_notification(server, "{\"status\":\"ready\",\"message\":\"WiFi service notifications enabled\"}");
	}
}

static void descriptor_read_cb(struct gatt_db_attribute *attrib,
				unsigned int id, uint16_t offset,
				uint8_t opcode, struct bt_att *att,
				void *user_data)
{
	const char *desc_value = "WiFi Configuration";
	uint16_t len = strlen(desc_value);

	printf("[DEBUG] descriptor_read_cb called\n");
	printf("[DEBUG] - id: %u\n", id);
	printf("[DEBUG] - offset: %u\n", offset);
	printf("[DEBUG] - opcode: 0x%02x\n", opcode);
	printf("[DEBUG] - descriptor value: %s\n", desc_value);

	if (offset > len) {
		printf("[DEBUG] - offset too large, sending error\n");
		gatt_db_attribute_read_result(attrib, id, BT_ATT_ERROR_INVALID_OFFSET, NULL, 0);
		return;
	}

	len -= offset;
	printf("[DEBUG] - sending response with %u bytes\n", len);
	gatt_db_attribute_read_result(attrib, id, 0, (uint8_t *)(desc_value + offset), len);
}

// 将16字节UUID转为字符串
static void uuid2str(const uint8_t *uuid, char *str, size_t str_len)
{
    snprintf(str, str_len,
        "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        uuid[0], uuid[1], uuid[2], uuid[3],
        uuid[4], uuid[5],
        uuid[6], uuid[7],
        uuid[8], uuid[9],
        uuid[10], uuid[11], uuid[12], uuid[13], uuid[14], uuid[15]);
}

static void gap_device_name_read_cb(struct gatt_db_attribute *attrib,
                unsigned int id, uint16_t offset,
                uint8_t opcode, struct bt_att *att,
                void *user_data)
{
    const char *name = get_device_name();
    uint16_t len = strlen(name);

    printf("[DEBUG] GAP device name read: %s\n", name);

    if (offset > len) {
        gatt_db_attribute_read_result(attrib, id, BT_ATT_ERROR_INVALID_OFFSET, NULL, 0);
        return;
    }

    len -= offset;
    gatt_db_attribute_read_result(attrib, id, 0, (uint8_t *)(name + offset), len);
}

static void gap_appearance_read_cb(struct gatt_db_attribute *attrib,
                unsigned int id, uint16_t offset,
                uint8_t opcode, struct bt_att *att,
                void *user_data)
{
    uint8_t appearance[2] = { 0x00, 0x00 }; // Unknown appearance

    printf("[DEBUG] GAP appearance read\n");

    if (offset > 2) {
        gatt_db_attribute_read_result(attrib, id, BT_ATT_ERROR_INVALID_OFFSET, NULL, 0);
        return;
    }

    gatt_db_attribute_read_result(attrib, id, 0, appearance + offset, 2 - offset);
}

static void populate_gap_service(struct server *server)
{
    struct gatt_db_attribute *service;
    bt_uuid_t uuid;

    printf("[DEBUG] Populating GAP service\n");

    // Add GAP service
    bt_uuid16_create(&uuid, 0x1800); // Generic Access Profile
    service = gatt_db_add_service(server->db, &uuid, true, 6);

    // Device Name characteristic
    bt_uuid16_create(&uuid, 0x2A00); // Device Name
    gatt_db_service_add_characteristic(service, &uuid,
            BT_ATT_PERM_READ,
            BT_GATT_CHRC_PROP_READ,
            gap_device_name_read_cb, NULL, server);

    // Appearance characteristic
    bt_uuid16_create(&uuid, 0x2A01); // Appearance
    gatt_db_service_add_characteristic(service, &uuid,
            BT_ATT_PERM_READ,
            BT_GATT_CHRC_PROP_READ,
            gap_appearance_read_cb, NULL, server);

    gatt_db_service_set_active(service, true);
    printf("[DEBUG] GAP service populated\n");
}

static void populate_gatt_service(struct server *server)
{
    struct gatt_db_attribute *service, *characteristic;
    bt_uuid_t uuid;

    printf("[DEBUG] Populating GATT service\n");

    // Add GATT service
    bt_uuid16_create(&uuid, 0x1801); // Generic Attribute Profile
    service = gatt_db_add_service(server->db, &uuid, true, 6); // Increased size for CCCD

    // Service Changed characteristic with proper CCCD
    bt_uuid16_create(&uuid, 0x2A05); // Service Changed
    characteristic = gatt_db_service_add_characteristic(service, &uuid,
            BT_ATT_PERM_READ,
            BT_GATT_CHRC_PROP_INDICATE,
            NULL, NULL, server);

    // Add Client Characteristic Configuration Descriptor for Service Changed
    bt_uuid16_create(&uuid, 0x2902); // CCCD
    gatt_db_service_add_descriptor(characteristic, &uuid,
            BT_ATT_PERM_READ | BT_ATT_PERM_WRITE,
            cccd_read_cb, cccd_write_cb, server);

    gatt_db_service_set_active(service, true);
    printf("[DEBUG] GATT service populated with CCCD\n");
}

static void debug_uuid_conversion(const char *uuid_str, const char *context)
{
    uint128_t uuid_value;
    char converted_str[37];
    
    printf("[DEBUG] %s - Converting UUID: %s\n", context, uuid_str);
    str2uuid(uuid_str, (uint8_t *)&uuid_value, 16);
    uuid2str((const uint8_t *)&uuid_value, converted_str, sizeof(converted_str));
    printf("[DEBUG] %s - Converted to: %s\n", context, converted_str);
    
    // Print raw bytes
    printf("[DEBUG] %s - Raw bytes: ", context);
    for (int i = 0; i < 16; i++) {
        printf("%02x ", ((uint8_t *)&uuid_value)[i]);
    }
    printf("\n");
}

static void populate_wifi_service(struct server *server)
{
    struct gatt_db_attribute *service, *characteristic;
    bt_uuid_t uuid;
    uint128_t uuid_value;

#if 0
    printf("[DEBUG] ================== BUILDING WIFI SERVICE ==================\n");
    printf("[DEBUG] Using original 128-bit UUIDs as required by protocol\n");
    printf("[DEBUG] WiFi service UUID: %s\n", LINUXBOX_SERVICE_UUID_STR);
    printf("[DEBUG] WiFi char UUID: %s\n", WIFI_CONFIG_CHAR_UUID_STR);
#endif

    // Add WiFi configuration service using 128-bit UUID
    str2uuid(LINUXBOX_SERVICE_UUID_STR, (uint8_t *)&uuid_value, 16);
    bt_uuid128_create(&uuid, uuid_value);
    
    printf("[DEBUG] Creating WiFi service with 4 attributes\n");
    service = gatt_db_add_service(server->db, &uuid, true, 4);
    if (!service) {
        printf("[ERROR] Failed to create WiFi service!\n");
        return;
    }
    printf("[DEBUG] WiFi service created successfully\n");

    // Add WiFi configuration characteristic using 128-bit UUID
    str2uuid(WIFI_CONFIG_CHAR_UUID_STR, (uint8_t *)&uuid_value, 16);
    bt_uuid128_create(&uuid, uuid_value);
    
    printf("[DEBUG] Adding WiFi characteristic with WRITE|WRITE_NO_RESPONSE|NOTIFY properties\n");
    characteristic = gatt_db_service_add_characteristic(service, &uuid,
            BT_ATT_PERM_WRITE,
            BT_GATT_CHRC_PROP_WRITE | BT_GATT_CHRC_PROP_WRITE_WITHOUT_RESP | BT_GATT_CHRC_PROP_NOTIFY,
            NULL, wifi_config_write_cb, server);
    
    if (!characteristic) {
        printf("[ERROR] Failed to create WiFi characteristic!\n");
        return;
    }
    printf("[DEBUG] WiFi characteristic created successfully\n");

    // Add client characteristic configuration descriptor (0x2902)
    bt_uuid16_create(&uuid, 0x2902);
    printf("[DEBUG] Adding CCCD descriptor\n");
    if (!gatt_db_service_add_descriptor(characteristic, &uuid,
            BT_ATT_PERM_READ | BT_ATT_PERM_WRITE,
            cccd_read_cb, cccd_write_cb, server)) {
        printf("[ERROR] Failed to add CCCD descriptor!\n");
        return;
    }
    printf("[DEBUG] CCCD descriptor added successfully\n");

    server->chara_att = characteristic;
    server->chara_handle = gatt_db_attribute_get_handle(characteristic);

    printf("[DEBUG] Activating WiFi service\n");
    if (!gatt_db_service_set_active(service, true)) {
        printf("[ERROR] Failed to activate WiFi service!\n");
        return;
    }

#if 0
    printf("[DEBUG] WiFi service activated successfully\n");
    printf("[DEBUG] WiFi characteristic handle: 0x%04x\n", server->chara_handle);
    printf("[DEBUG] WiFi service UUID: %s\n", LINUXBOX_SERVICE_UUID_STR);
    printf("[DEBUG] WiFi characteristic UUID: %s\n", WIFI_CONFIG_CHAR_UUID_STR);
    printf("[DEBUG] ================= WIFI SERVICE COMPLETE =================\n");
#endif
}

static struct server *server_create(int fd)
{
	struct server *server;

    printf("[DEBUG] Creating server with fd: %d\n", fd);

    server = malloc(sizeof(*server));
	if (!server) {
        printf("[DEBUG] Failed to allocate server\n");
		return NULL;
	}

    memset(server, 0, sizeof(*server));
    server->fd = fd;

	server->att = bt_att_new(fd, false);
	if (!server->att) {
        printf("[DEBUG] Failed to create ATT\n");
        free(server);
        return NULL;
	}

	if (!bt_att_set_close_on_unref(server->att, true)) {
        printf("[DEBUG] Failed to set close on unref\n");
        bt_att_unref(server->att);
        free(server);
        return NULL;
    }

    bt_att_register_disconnect(server->att, att_disconnect_cb, server, NULL);

    if (verbose) {
        bt_att_set_debug(server->att, BT_ATT_DEBUG_VERBOSE, att_debug_cb, "att: ", NULL);
    }

	server->db = gatt_db_new();
	if (!server->db) {
        printf("[DEBUG] Failed to create GATT DB\n");
        bt_att_unref(server->att);
        free(server);
        return NULL;
	}

    // Use MTU (23) - fixed MTU value
	server->gatt = bt_gatt_server_new(server->db, server->att, 23, 0);
	if (!server->gatt) {
        printf("[DEBUG] Failed to create GATT server\n");
        gatt_db_unref(server->db);
        bt_att_unref(server->att);
        free(server);
        return NULL;
	}

	if (verbose) {
        bt_gatt_server_set_debug(server->gatt, gatt_debug_cb, "server: ", NULL);
    }

    // Initialize notification state
    server->notifying = false;
    server->notification_ready = false;
    pthread_mutex_init(&server->notification_lock, NULL);

    printf("[DEBUG] ================== BUILDING GATT DATABASE ==================\n");
    
    // Populate standard services first
    populate_gap_service(server);
    populate_gatt_service(server);
    
    // Then populate our custom WiFi service
    populate_wifi_service(server);
#if 0
    printf("[DEBUG] ================== GATT DATABASE COMPLETE ==================\n");
    printf("[DEBUG] Services built:\n");
    printf("[DEBUG] 1. GAP Service (0x1800) - Device Name & Appearance\n");
    printf("[DEBUG] 2. GATT Service (0x1801) - Service Changed with CCCD\n");
    printf("[DEBUG] 3. WiFi Service (%s) - WiFi Configuration\n", LINUXBOX_SERVICE_UUID_STR);
    printf("[DEBUG] ================================================================\n");
#endif
    printf("[DEBUG] Server created successfully with fixed MTU=23\n");
    return server;
}

static void server_destroy(struct server *server)
{
	bt_gatt_server_unref(server->gatt);
	gatt_db_unref(server->db);
}



static int l2cap_le_att_listen_and_accept(bdaddr_t *src, int sec,
		uint8_t src_type)
{
	int sk, nsk, i;
	struct sockaddr_l2 srcaddr, addr;
	socklen_t optlen;
	struct bt_security btsec;
	char ba[18];

	sk = socket(PF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP);
	if (sk < 0) {
		perror("Failed to create L2CAP socket");
		return -1;
	}

	/* Set up source address */
	memset(&srcaddr, 0, sizeof(srcaddr));
	srcaddr.l2_family = AF_BLUETOOTH;
	srcaddr.l2_cid = htobs(ATT_CID);
	srcaddr.l2_bdaddr_type = src_type;
	bacpy(&srcaddr.l2_bdaddr, src);
	printf("\n");
	if (bind(sk, (struct sockaddr *) &srcaddr, sizeof(srcaddr)) < 0) {
		perror("Failed to bind L2CAP socket");
		goto fail;
	}

	/* Set the security level */
	memset(&btsec, 0, sizeof(btsec));
	btsec.level = sec;
	if (setsockopt(sk, SOL_BLUETOOTH, BT_SECURITY, &btsec,
				sizeof(btsec)) != 0) {
		fprintf(stderr, "Failed to set L2CAP security level\n");
		goto fail;
	}

	if (listen(sk, 10) < 0) {
		perror("Listening on socket failed");
		goto fail;
	}

	printf("Started listening on ATT channel. Waiting for connections\n");

	// Use select to make accept interruptible by signals
	fd_set readfds;
	int max_fd = sk;
	int select_count = 0;
	time_t start_time = time(NULL);
	
	printf("[SELECT] Entering select loop, should_exit = %d\n", should_exit);
	printf("[SELECT] Starting %d-second timeout for no client connection\n", user_timeout_seconds);
	
	// Setup signal mask for pselect
	sigset_t empty_mask, blocked_mask;
	sigemptyset(&empty_mask);
	sigemptyset(&blocked_mask);
	sigaddset(&blocked_mask, SIGINT);
	sigaddset(&blocked_mask, SIGTERM);
	
	while (!should_exit) {
		// Check should_exit at the very beginning of each loop iteration
		if (should_exit) {
			printf("[SELECT] should_exit = true detected at loop start, breaking\n");
			break;
		}
		
		FD_ZERO(&readfds);
		FD_SET(sk, &readfds);
		
		// Use pselect with a timeout to periodically check should_exit
		struct timespec timeout;
		timeout.tv_sec = 1;  // 1 second timeout
		timeout.tv_nsec = 0;
		
		// Temporarily unblock signals during pselect
		int ret = pselect(max_fd + 1, &readfds, NULL, NULL, &timeout, &empty_mask);
		select_count++;
		
		// Check if we've exceeded the no-client timeout
		time_t current_time = time(NULL);
		if (current_time - start_time >= user_timeout_seconds) {
			printf("[SELECT] No client connected for %d seconds, exiting...\n", user_timeout_seconds);
			send_socket_command(LED_SYS_EVENT_OFF);
			should_exit = true;
			goto fail;
		}
		
		if (ret < 0) {
			if (errno == EINTR) {
				// Interrupted by signal, check should_exit
				printf("[SELECT] Interrupted by signal (count: %d), should_exit = %d\n", select_count, should_exit);
				continue;
			}
			perror("Select failed");
			goto fail;
		} else if (ret == 0) {
			// Timeout, continue to check should_exit
			if (select_count % 10 == 0) { // Print every 10 seconds
				printf("[SELECT] Timeout (count: %d), should_exit = %d, elapsed = %ld/%d seconds\n", 
				       select_count, should_exit, current_time - start_time, user_timeout_seconds);
				printf("[SELECT] Press Ctrl+C to exit immediately\n");
			}
			// Explicitly check should_exit after each timeout
			if (should_exit) {
				printf("[SELECT] should_exit detected during timeout, breaking\n");
				break;
			}
			continue;
		} else if (FD_ISSET(sk, &readfds)) {
			// Socket is ready for accept
			printf("[SELECT] Socket ready for accept (count: %d)\n", select_count);
			break;
		}
	}
	
	// Check if we should exit before accepting
	if (should_exit) {
		printf("Exiting before accepting connection\n");
		goto fail;
	}

	memset(&addr, 0, sizeof(addr));
	optlen = sizeof(addr);
	nsk = accept(sk, (struct sockaddr *) &addr, &optlen);
	if (nsk < 0) {
		perror("Accept failed");
		goto fail;
	}

	ba2str(&addr.l2_bdaddr, ba);
	printf("Connect from %s\n", ba);
	close(sk);

	return nsk;

fail:
	close(sk);
	return -1;
}


// Standard signal handler function
static void signal_handler(int signum)
{
    // Use write() instead of printf() as it's signal-safe
    const char msg[] = "\n[SIGNAL] Signal received, setting should_exit = true\n";
    write(STDOUT_FILENO, msg, sizeof(msg) - 1);
    
    switch (signum) {
    case SIGINT:
        {
            const char sigint_msg[] = "[SIGNAL] SIGINT (Ctrl+C) received!\n";
            write(STDOUT_FILENO, sigint_msg, sizeof(sigint_msg) - 1);
            should_exit = true;
            mainloop_quit();
            break;
        }
    case SIGTERM:
        {
            const char sigterm_msg[] = "[SIGNAL] SIGTERM received!\n";
            write(STDOUT_FILENO, sigterm_msg, sizeof(sigterm_msg) - 1);
            
            // Immediately stop advertising when SIGTERM is received
            if (advertising) {
                const char adv_msg[] = "[SIGNAL] Stopping advertising due to SIGTERM\n";
                write(STDOUT_FILENO, adv_msg, sizeof(adv_msg) - 1);
                
                // Use direct HCI commands to stop advertising (signal-safe)
                struct bt_hci_cmd_le_set_adv_enable param;
                param.enable = 0;
                
                // Note: This is a simplified approach - in production, you might want
                // to use a more robust method like writing to a pipe or using atexit()
                int hdev = hci_get_route(NULL);
                if (hdev >= 0) {
                    int dd = hci_open_dev(hdev);
                    if (dd >= 0) {
                        struct hci_request rq;
                        uint8_t status;
                        memset(&rq, 0, sizeof(rq));
                        rq.ogf = OGF_LE_CTL;
                        rq.ocf = BT_HCI_CMD_LE_SET_ADV_ENABLE;
                        rq.cparam = &param;
                        rq.clen = sizeof(param);
                        rq.rparam = &status;
                        rq.rlen = 1;
                        hci_send_req(dd, &rq, 1000);
                        hci_close_dev(dd);
                    }
                }
                advertising = false;
            }
            
            should_exit = true;
            mainloop_quit();
            break;
        }
    default:
        break;
    }
}

static void signal_cb(int signum, void *user_data)
{
    switch (signum) {
    case SIGINT:
    case SIGTERM:
        printf("\n[SIGNAL] Received termination signal (%d)\n", signum);
        send_socket_command(LED_SYS_EVENT_OFF);
        should_exit = true;
        mainloop_quit();
        break;
    default:
        break;
    }
}

static void send_cmd(int cmd, void *params, int params_len)
{
	struct hci_request rq;
	uint8_t status;
	int dd, ret, hdev;

	hdev = hci_get_route(NULL);
	if (hdev < 0) {
		perror("Could not get HCI device");
		exit(1);
	}

	dd = hci_open_dev(hdev);
	if (dd < 0) {
		perror("Could not open device");
		exit(1);
	}

	memset(&rq, 0, sizeof(rq));
	rq.ogf = OGF_LE_CTL;
	rq.ocf = cmd;
	rq.cparam = params;
	rq.clen = params_len;
	rq.rparam = &status;
	rq.rlen = 1;

	ret = hci_send_req(dd, &rq, 1000);

done:
	hci_close_dev(dd);

	if (ret < 0) {
		fprintf(stderr, "Can't send cmd 0x%x to hci%d: %s (%d)\n", cmd,
				hdev, strerror(errno), errno);
		exit(1);
	}

	if (status) {
		fprintf(stderr,
				"LE cmd 0x%x on hci%d returned status %d\n", cmd,
				hdev, status);
		exit(1);
	}
}


// Device name generation - unified approach
static char device_name_cache[32] = {0};
static bool device_name_initialized = false;

static const char* get_device_name(void)
{
    if (device_name_initialized) {
        return device_name_cache;
    }
    
    char mac_str[18];
    bool mac_obtained = false;
    
    printf("[DEVICE_NAME] Generating device name...\n");
    
    // Try to get MAC address with retry logic
    if (get_wifi_mac(mac_str) == 0 && strlen(mac_str) > 0) {
        // Convert MAC address to uppercase
        for (int i = 0; mac_str[i]; i++) {
            mac_str[i] = toupper(mac_str[i]);
        }
        
        // Use only last 8 characters of MAC address, consistent with Python logic
        size_t mac_len = strlen(mac_str);
        if (mac_len >= 8) {
            snprintf(device_name_cache, sizeof(device_name_cache), 
                    "3RHUB-%s", mac_str + mac_len - 8);
        } else {
            snprintf(device_name_cache, sizeof(device_name_cache), 
                    "3RHUB-%s", mac_str);
        }
        mac_obtained = true;
        printf("[DEVICE_NAME] Generated from MAC: %s\n", device_name_cache);
    } else {
        // Fallback strategies
        printf("[DEVICE_NAME] MAC address not available, using fallback methods...\n");
        
        // Option 1: Try to get last 6 characters of machine-id
        FILE *f = fopen("/etc/machine-id", "r");
        if (f != NULL) {
            char machine_id[64] = {0};
            if (fgets(machine_id, sizeof(machine_id), f) != NULL) {
                size_t len = strlen(machine_id);
                if (len >= 6) {
                    // Remove newline and take last 6 characters
                    if (machine_id[len-1] == '\n') {
                        machine_id[len-1] = '\0';
                        len--;
                    }
                    if (len >= 6) {
                        snprintf(device_name_cache, sizeof(device_name_cache), 
                                "3RHUB-%s", machine_id + len - 6);
                        mac_obtained = true;
                        printf("[DEVICE_NAME] Generated from machine-id: %s\n", device_name_cache);
                    }
                }
            }
            fclose(f);
        }
        
        // Option 2: Use timestamp-based suffix if machine-id failed
        if (!mac_obtained) {
            time_t now = time(NULL);
            snprintf(device_name_cache, sizeof(device_name_cache), 
                    "3RHUB-%04lX", (unsigned long)(now & 0xFFFF));
            printf("[DEVICE_NAME] Generated from timestamp: %s\n", device_name_cache);
        }
    }

    // Final validation and fallback
    if (strlen(device_name_cache) == 0) {
        strcpy(device_name_cache, "3RHUB-DEFAULT");
        printf("[DEVICE_NAME] Emergency fallback: %s\n", device_name_cache);
    }
    
    device_name_initialized = true;
    printf("[DEVICE_NAME] Final device name: %s\n", device_name_cache);
    return device_name_cache;
}

static int get_wifi_mac(char* mac_buf)
{
    int result = -1;
    int retry_count = 0;
    const int max_retries = 3;
    const int retry_delay_ms = 500; // 500ms delay between retries

#if TEST_MAC_ADDRESS
    // Test mode: return fixed MAC address without colons
    strcpy(mac_buf, "8C1D96B9FEEC");
    return 0;
#else
    
    while (retry_count < max_retries && result != 0) {
        if (retry_count > 0) {
            printf("[MAC] Retry attempt %d/%d after %dms delay\n", 
                   retry_count + 1, max_retries, retry_delay_ms);
            usleep(retry_delay_ms * 1000); // Convert to microseconds
        }
        
        FILE* f = popen("ip link show wlan0 | grep -o -E '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' | tr -d ':'", "r");
        if (f == NULL) {
            fprintf(stderr, "[MAC] Failed to execute command (attempt %d/%d)\n", 
                    retry_count + 1, max_retries);
            retry_count++;
            continue;
        }

        char temp_buf[20] = {0}; // Temporary buffer for validation
        if (fgets(temp_buf, sizeof(temp_buf), f) != NULL) {
            size_t len = strlen(temp_buf);
            if (len > 0 && temp_buf[len - 1] == '\n') {
                temp_buf[len - 1] = '\0';
                len--;
            }
            
            // Validate MAC address: should be exactly 12 hex characters
            if (len == 12) {
                bool valid = true;
                for (int i = 0; i < 12; i++) {
                    if (!isxdigit(temp_buf[i])) {
                        valid = false;
                        break;
                    }
                }
                
                if (valid) {
                    strcpy(mac_buf, temp_buf);
                    result = 0;
                    printf("[MAC] Successfully obtained MAC address: %s (attempt %d/%d)\n", 
                           mac_buf, retry_count + 1, max_retries);
                } else {
                    printf("[MAC] Invalid MAC format received: '%s' (attempt %d/%d)\n", 
                           temp_buf, retry_count + 1, max_retries);
                }
            } else {
                printf("[MAC] Invalid MAC length %zu, expected 12 (attempt %d/%d)\n", 
                       len, retry_count + 1, max_retries);
            }
        } else {
            printf("[MAC] No output from command (attempt %d/%d)\n", 
                   retry_count + 1, max_retries);
        }

        pclose(f);
        retry_count++;
    }
    
    if (result != 0) {
        fprintf(stderr, "[MAC] Failed to get wlan0 MAC address after %d attempts\n", max_retries);
    }
    
    return result;
#endif
}

static void set_adv_parameters(void)
{
	struct bt_hci_cmd_le_set_adv_parameters param;

    param.own_addr_type = 0x00;    /* Use public address */
    // Use much longer intervals for maximum stability against timeouts
	param.min_interval = cpu_to_le16(0x0100);  // 160ms for maximum stability
	param.max_interval = cpu_to_le16(0x0200);  // 320ms for very slow but stable advertising
    param.type = 0x00;        /* connectable no-direct advertising */
	param.direct_addr_type = 0x00;
	memset(param.direct_addr, 0, 6);
	param.channel_map = 0x07;
	param.filter_policy = 0x00;

    printf("[DEBUG] Setting advertising parameters with longer intervals for stability\n");
	send_cmd(BT_HCI_CMD_LE_SET_ADV_PARAMETERS, (void *)&param, sizeof(param));
}

static void set_adv_enable(int enable)
{
    struct bt_hci_cmd_le_set_adv_enable param;
    if (enable !=0 && enable != 1) {
        printf("%s: invalid arg: \n", __func__, enable);
        return;
    }
    param.enable = enable;
    send_cmd(BT_HCI_CMD_LE_SET_ADV_ENABLE, (void *)&param, sizeof(param));
}

static void set_adv_response(void)
{
    struct bt_hci_cmd_le_set_scan_rsp_data param;
    const char *device_name = get_device_name();
    
    printf("[ADV] Using unified device name: %s\n", device_name);

    memset(&param, 0, sizeof(param));
    param.len = 0;

    // Validate device name length for BLE advertising
    size_t name_len = strlen(device_name);
    if (name_len > 29) { // BLE scan response has ~31 byte limit, need 2 bytes for length+type
        name_len = 29;
        printf("[ADV] Device name truncated to %zu characters\n", name_len);
    }

    // Add local name
    param.data[param.len++] = name_len + 1;  // Length including type
    param.data[param.len++] = 0x09;          // Complete Local Name
    memcpy(&param.data[param.len], device_name, name_len);
    param.len += name_len;

    printf("[ADV] Scan response data length: %d bytes\n", param.len);
    printf("[ADV] Scan response data: ");
    for (int i = 0; i < param.len; i++) {
        printf("%02X ", param.data[i]);
    }
    printf("\n");
    printf("[ADV] Final device name: %.*s (length: %zu)\n", (int)name_len, device_name, name_len);

    send_cmd(BT_HCI_CMD_LE_SET_SCAN_RSP_DATA, &param, sizeof(param));
}

static void set_adv_data(void)
{
    struct bt_hci_cmd_le_set_adv_data param;
    uint128_t uuid_value;
    
    memset(&param, 0, sizeof(param));
    param.len = 0;

    // Add flags
    param.data[param.len++] = 2;
    param.data[param.len++] = 0x01;
    param.data[param.len++] = 0x04;  // LE General Discoverable Mode

    // Add 128-bit service UUID
    str2uuid(LINUXBOX_SERVICE_UUID_STR, (uint8_t *)&uuid_value, 16);
    param.data[param.len++] = 17;  // Length: 1 byte type + 16 bytes UUID
    param.data[param.len++] = 0x07;  // Complete List of 128-bit Service UUIDs
    // 修正：BLE 广播包要求 UUID 用 little-endian 顺序
    for (int i = 0; i < 16; i++) {
        param.data[param.len + i] = ((uint8_t *)&uuid_value)[15 - i];
    }
    param.len += 16;

    // Add TX power
    param.data[param.len++] = 2;
    param.data[param.len++] = 0x0A;
    param.data[param.len++] = 0x00;

    printf("Advertising data length: %d bytes\n", param.len);
    printf("Advertising data: ");
    for (int i = 0; i < param.len; i++) {
        printf("%02X ", param.data[i]);
    }
    printf("\n");
    printf("Service UUID: %s\n", LINUXBOX_SERVICE_UUID_STR);

    send_cmd(BT_HCI_CMD_LE_SET_ADV_DATA, &param, sizeof(param));
}

void ble_init(void)
{
    printf("GATT server, initialize BLE settings.\n");
    
    // Disable advertising first
    set_adv_enable(0);
    
    printf("GATT server[1].\n");
    set_adv_parameters();

    printf("GATT server[2].\n");
    set_adv_data();
    set_adv_response();

    printf("GATT server[3].\n");
    set_adv_enable(1);

    printf("GATT server, finish initialize BLE settings.\n");
}



void hci_dev_init(void)
{
	printf("GATT server, initialize devices.\n");
	/* Open HCI socket	*/
	if ((ctl = socket(AF_BLUETOOTH, SOCK_RAW, BTPROTO_HCI)) < 0) {
		perror("Can't open HCI socket.");
		exit(1);
	}

	hdi.dev_id = 0;

	if (ioctl(ctl, HCIGETDEVINFO, (void *) &hdi)) {
		perror("Can't get device info");
		exit(1);
	}

}


// Advertising control functions
static void start_advertising(void)
{
    if (!advertising) {
        printf("[ADV] Starting advertising...\n");
        fflush(stdout);
        
        // First ensure advertising is disabled
        printf("[ADV] Disabling advertising first...\n");
        fflush(stdout);
        set_adv_enable(0);
        usleep(100000); // Wait 100ms
        
        // Reset advertising parameters
        printf("[ADV] Setting advertising parameters...\n");
        fflush(stdout);
        set_adv_parameters();
        
        printf("[ADV] Setting advertising data...\n");
        fflush(stdout);
        set_adv_data();
        
        printf("[ADV] Setting scan response data...\n");
        fflush(stdout);
        set_adv_response();
        
        // Enable advertising
        printf("[ADV] Enabling advertising...\n");
        fflush(stdout);
        set_adv_enable(1);
        
        // Wait a moment for advertising to start
        usleep(50000); // 50ms
        
        advertising = true;
        printf("[ADV] Advertising restarted successfully\n");
        printf("[ADV] Device should now be visible as: %s\n", get_device_name());
        fflush(stdout);
    } else {
        printf("[ADV] Advertising already running\n");
        fflush(stdout);
    }
}

static void stop_advertising(void)
{
    if (advertising) {
        printf("[ADV] Stopping advertising...\n");
        fflush(stdout);
        set_adv_enable(0);
        usleep(50000); // 50ms
        set_adv_enable(0); // double check

        // Clear advertising data
        struct bt_hci_cmd_le_set_adv_data adv_clear;
        memset(&adv_clear, 0, sizeof(adv_clear));
        adv_clear.len = 0;
        send_cmd(BT_HCI_CMD_LE_SET_ADV_DATA, &adv_clear, sizeof(adv_clear));

        // Clear scan response data
        struct bt_hci_cmd_le_set_scan_rsp_data scan_clear;
        memset(&scan_clear, 0, sizeof(scan_clear));
        scan_clear.len = 0;
        send_cmd(BT_HCI_CMD_LE_SET_SCAN_RSP_DATA, &scan_clear, sizeof(scan_clear));

        advertising = false;
        printf("[ADV] Advertising stopped and data cleared\n");
        fflush(stdout);
    } else {
        printf("[ADV] Advertising already stopped\n");
        fflush(stdout);
    }
}

// No client timeout handling
static void no_client_timeout_cb(int timeout_id, void *user_data)
{
    printf("[TIMEOUT] No client connected for %d seconds, exiting...\n", 
           user_timeout_seconds);
    
    // Send LED off command
    send_socket_command(LED_SYS_EVENT_OFF);
    
    should_exit = true;
    mainloop_quit();
}

// Reset timeout timer
static void reset_no_client_timeout(void)
{
    if (no_client_timeout_id > 0) {
        mainloop_remove_timeout(no_client_timeout_id);
        no_client_timeout_id = 0;
    }
    
    // Only start timeout when no client connected
    if (!client_connected) {
        no_client_timeout_id = mainloop_add_timeout(user_timeout_seconds * 1000,
                                                   no_client_timeout_cb, NULL, NULL);
        printf("[TIMEOUT] Started %d second timeout for no client connection\n", 
               user_timeout_seconds);
    }
}

// Global cleanup function for atexit
static void cleanup_on_exit(void)
{
    if (advertising) {
        printf("[CLEANUP] Cleaning up advertising on exit\n");
        fflush(stdout);
        
        // Force stop advertising
        struct bt_hci_cmd_le_set_adv_enable param;
        param.enable = 0;
        
        int hdev = hci_get_route(NULL);
        if (hdev >= 0) {
            int dd = hci_open_dev(hdev);
            if (dd >= 0) {
                struct hci_request rq;
                uint8_t status;
                memset(&rq, 0, sizeof(rq));
                rq.ogf = OGF_LE_CTL;
                rq.ocf = BT_HCI_CMD_LE_SET_ADV_ENABLE;
                rq.cparam = &param;
                rq.clen = sizeof(param);
                rq.rparam = &status;
                rq.rlen = 1;
                hci_send_req(dd, &rq, 1000);
                hci_close_dev(dd);
            }
        }
        advertising = false;
    }
}

int main(int argc, char *argv[])
{

	bdaddr_t src_addr;
	int fd;
	int sec = BT_SECURITY_LOW;
	uint8_t src_type = BDADDR_LE_PUBLIC;
	int opt;

	// Register cleanup function for all exit paths
	atexit(cleanup_on_exit);

	// Set stdout to unbuffered for immediate log output
	setvbuf(stdout, NULL, _IONBF, 0);
	setvbuf(stderr, NULL, _IONBF, 0);

	// Parse command line arguments
	while ((opt = getopt(argc, argv, "t:v")) != -1) {
		switch (opt) {
		case 't':
			user_timeout_seconds = atoi(optarg);
			if (user_timeout_seconds <= 0) {
				fprintf(stderr, "Invalid timeout value: %s\n", optarg);
				return EXIT_FAILURE;
			}
			break;
		case 'v':
			verbose = true;
			break;
		default:
			fprintf(stderr, "Usage: %s [-t timeout_seconds] [-v]\n", argv[0]);
			fprintf(stderr, "  -t timeout_seconds: Set timeout for no client connection (default: 300)\n");
			fprintf(stderr, "  -v: Enable verbose mode\n");
			return EXIT_FAILURE;
		}
	}

	printf("[MAIN] === GATT WiFi Configuration Server Starting ===\n");
	printf("[MAIN] Version: v1.0.2\n");
	printf("[MAIN] Service UUID: %s\n", LINUXBOX_SERVICE_UUID_STR);
	printf("[MAIN] Characteristic UUID: %s\n", WIFI_CONFIG_CHAR_UUID_STR);
	printf("[MAIN] Timeout: %d seconds\n", user_timeout_seconds);
	printf("[MAIN] ======================================== ===\n");

	// Set signal handlers using sigaction to ensure proper interruption
	struct sigaction sa;
	memset(&sa, 0, sizeof(sa));
	sa.sa_handler = signal_handler;
	sigemptyset(&sa.sa_mask);
	sa.sa_flags = 0;  // Do not set SA_RESTART to ensure select() is interrupted
	
	if (sigaction(SIGINT, &sa, NULL) == -1) {
		perror("sigaction SIGINT");
		return EXIT_FAILURE;
	}
	
	if (sigaction(SIGTERM, &sa, NULL) == -1) {
		perror("sigaction SIGTERM");
		return EXIT_FAILURE;
	}
	
	// Test signal handling immediately
	printf("[SIGNAL] Signal handlers installed for SIGINT and SIGTERM\n");
	printf("[SIGNAL] Testing signal handling - should_exit = %d\n", should_exit);
	
	// Test signal handling by printing current should_exit status
	printf("[DEBUG] Initial should_exit = %d\n", should_exit);
	
	// Block signals except during pselect calls
	sigset_t blocked_signals;
	sigemptyset(&blocked_signals);
	sigaddset(&blocked_signals, SIGINT);
	sigaddset(&blocked_signals, SIGTERM);
	if (sigprocmask(SIG_BLOCK, &blocked_signals, NULL) == -1) {
		perror("sigprocmask");
		return EXIT_FAILURE;
	}
	printf("[SIGNAL] Signals blocked except during pselect calls\n");

	// Send LED command on startup
	send_socket_command(LED_SYS_WIFI_CONFIG_PENDING);

	// Enable verbose mode for better debugging
	verbose = true;

	hci_dev_init();

	// Main loop: continuously listen for connections
	while (!should_exit) {
		// Re-confirm signal handlers are still active (in case mainloop overrode them)
		if (sigaction(SIGINT, &sa, NULL) == -1) {
			perror("sigaction SIGINT re-install");
		}
		if (sigaction(SIGTERM, &sa, NULL) == -1) {
			perror("sigaction SIGTERM re-install");
		}
		printf("[SIGNAL] Signal handlers re-confirmed before restart\n");
		printf("[DEBUG] Current should_exit = %d\n", should_exit);
		
		// Start advertising before listening
		printf("[MAIN] Starting advertising before listening for connections...\n");
		fflush(stdout);
		start_advertising();

		printf("[MAIN] Create GATT server l2cap_le_att_listen_and_accept ...\n");
		fflush(stdout);

		bacpy(&src_addr, BDADDR_ANY);
		fd = l2cap_le_att_listen_and_accept(&src_addr, sec, src_type);
		if (fd < 0) {
			fprintf(stderr, "Failed to accept L2CAP ATT connection\n");
			send_socket_command(LED_SYS_EVENT_OFF);

            printf("[DEBUG] SETTING_WIFI_NOTIFY[1]...\n");
			send_socket_command(SETTING_WIFI_NOTIFY);
			usleep(500000);
			return EXIT_FAILURE;
		}

		printf("[MAIN] Client connected! Creating GATT server...\n");
		
		// After client connects, stop advertising
		stop_advertising();
		client_connected = true;

		printf("[MAIN] Create GATT server main loop ...\n");
		mainloop_init();

		printf("[MAIN]Create GATT server...\n");
		server = server_create(fd);
		if (!server) {
			close(fd);
			send_socket_command(LED_SYS_EVENT_OFF);
            printf("[DEBUG] SETTING_WIFI_NOTIFY[2]...\n");
			send_socket_command(SETTING_WIFI_NOTIFY);
			usleep(500000);
			return EXIT_FAILURE;
		}

		// Start no-client timeout timer (since no client is connected yet)
		reset_no_client_timeout();

		printf("[ADV] === GATT Server Ready - Waiting for Android App ===\n");
		printf("[ADV] Device name: %s\n", get_device_name());
		printf("[ADV] Ready to receive WiFi configuration from Android app\n");
		printf("[ADV] No client timeout: %d seconds\n", user_timeout_seconds);
		printf("[ADV] ============================================= ===\n");

		mainloop_run_with_signal(signal_cb, NULL);
		
		printf("\n[MAIN] Mainloop exited, checking if should restart...\n");
		
		// If exiting due to signal, really exit
		if (should_exit) {
			printf("[MAIN] Exiting due to termination signal\n");
			break;
		}
		
		// Otherwise prepare to restart listening
		printf("[MAIN] Preparing to restart listening for new connections...\n");
		
		// Check WiFi success count, if >= 1 and client disconnected automatically, exit service
		if (wifi_success_count >= TEST_MAX_WIFI_SUCCESS_COUNT) {
			printf("[MAIN] WiFi success count >= 1 (%d), client disconnected automatically - exiting service\n", wifi_success_count);
			should_exit = true;
			break;
		}
		
		// Clean up current connection state
		client_connected = false;
		advertising = false;
		
		// Clean up timeout timer
		if (no_client_timeout_id > 0) {
			mainloop_remove_timeout(no_client_timeout_id);
			no_client_timeout_id = 0;
		}
		
		// Wait a moment before restarting
		sleep(1);
	}

	printf("\n\n[MAIN] Shutting down...\n");

	// Clean up resources
	if (no_client_timeout_id > 0) {
		mainloop_remove_timeout(no_client_timeout_id);
	}
	
	stop_advertising();
	server_destroy(server);
	
	// Send LED off command on exit
	if (!should_exit) {  // Avoid duplicate sending
		send_socket_command(LED_SYS_EVENT_OFF);
	}

    printf("[DEBUG] SETTING_WIFI_NOTIFY[3]...\n");
	send_socket_command(SETTING_WIFI_NOTIFY);
	usleep(800000);
	return EXIT_SUCCESS;
}
