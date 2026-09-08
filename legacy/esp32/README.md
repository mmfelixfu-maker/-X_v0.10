# Legacy ESP32 bridge

这里保存项目早期使用的 ESP32-S3 TCP/串口桥与固件。当前主链路是
`dglab-bridge.mjs` + DG-LAB V4 WebSocket Relay，不再依赖这里的文件。

- `esp32-bridge.ps1`：旧 TCP 到串口桥。
- `firmware/esp32s3-galaku/`：旧 ESP32-S3 固件工程。

除非需要维护旧硬件方案，否则无需运行或编译本目录内容。
