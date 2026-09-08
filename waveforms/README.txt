用户波形导入目录
================

最简单的方法：
1. 把 DungeonLab 导出的 .pulse 文件或包含 .pulse 的 ZIP 放进 inbox 文件夹。
2. 回到项目根目录，双击 manage-waveforms.bat。
3. 选择“扫描 inbox 并导入”。

default：分析成功、AI 默认可以选择和循环播放的原始波形。
rejected：格式无效或无法可靠分析的波形，不会提供给 AI。
duplicates：内容和名称完全相同的冗余副本；保留一份在 default，副本不重复提供给 AI。
processed：已扫描过的原始 ZIP/.pulse，避免下次重复导入。
catalog.json：自动生成的波形索引，含强度、频率、节奏、适用场景和设备帧。

不要手工编辑 catalog.json。需要停用某个波形时，请运行 manage-waveforms.bat。
