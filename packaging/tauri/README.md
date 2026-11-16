# Windows 复用的打包与迁移模块

本目录只保留 Windows 仍在使用的共享模块。正式构建入口见 [Windows 打包说明](../windows/README.md)。

- `build_support.py`：源码导出、隐私排除、哈希、下载和解包。
- `prepare_payload.py`：运行源码筛选、必需资源检查、浏览器裁剪、默认配置与预测总表种子准备；Windows 构建入口为 `../windows/prepare_payload.py`。
- `prepare_workspace.py`：托管源码安装、更新校验、备份和失败回滚。
- `workspace_layout.py`：用户目录迁移，保留账号、独立浏览器 profile、输出与样例。
- `migrate_forecast_data.py`：以 `--source 原工作目录 --destination 新用户数据目录` 迁入配置的数据总表并校验，保留已有不同文件。
- `migrate_samples.py`：迁移独立测试副本，校验哈希并保留本地修改。
- `version.py`：版本同步与发布说明检查；更新内容以根目录 `CHANGELOG.md` 为准。
- `release_notes.md`：安装说明。

业务总表、配置和登录态不进入 Git。正式构建使用已有配置解析出的总表并校验；CI 合成总表只用于检查。
