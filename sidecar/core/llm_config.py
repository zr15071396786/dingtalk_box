"""llm_config.py — sidecar 侧的 LLM 配置管理

职责：
- 读 / 写 config.yaml 中的 llm 段
- 用 DPAPI 加密 api_key 写 llm_secret.bin
- 读 llm_secret.bin 并解密（轻量检测 + 故障恢复）
- 提供 provider preset 查找

注意：本模块的 _data_dir 在测试里会被 monkeypatch。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

# 复用 ai_bridge 的 dpapi
from ai_bridge import dpapi, providers

# 内置 Qwen 默认配置（key 由 launcher / dev.py 从 .env 注入环境变量）
DEFAULT_PROVIDER_ID = "aliyun_dashscope"
DEFAULT_MODEL_ID = "qwen3.5-flash"


def _builtin_api_key() -> str:
    """读环境变量 DINGTALK_BOX_DEFAULT_QWEN_KEY。空 → 无内置默认。"""
    return os.environ.get("DINGTALK_BOX_DEFAULT_QWEN_KEY", "").strip()


def resolve_api_key() -> str:
    """返回当前可用的明文 API key（按优先级）：
    1) 用户在 LLM 配置页保存的自定义 key（从 llm_secret.bin 解密）
    2) 内置默认 key（环境变量 DINGTALK_BOX_DEFAULT_QWEN_KEY）

    都没有 → raise FileNotFoundError。
    调用方在 spawn ai_bridge 子进程时把这个 key 通过 env var 传过去，
    避免 ai_bridge 内部直接读 DPAPI 文件失败。
    """
    sp = _secret_path()
    if sp.is_file():
        try:
            v = dpapi.unprotect_from_file(str(sp))
            if v:
                return v
            # 文件在但内容为空（前端 skip_key=True + 清空两次等极端情况）
            # → fall through 到 builtin
        except Exception:
            # DPAPI 解密失败（密钥错 / 文件损坏）→ fall through 到 builtin
            pass
    builtin = _builtin_api_key()
    if builtin:
        return builtin
    raise FileNotFoundError(f"密钥文件不存在: {sp}")


def _data_dir() -> Path:
    """解析 %APPDATA%/DingTalkBox（开发模式可用 ENV 覆盖）"""
    if "DINGTALK_BOX_DATA_DIR" in os.environ:
        return Path(os.environ["DINGTALK_BOX_DATA_DIR"])
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "DingTalkBox"
    return Path.home() / ".config" / "DingTalkBox"


def _config_path() -> Path:
    return _data_dir() / "config.yaml"


def _secret_path() -> Path:
    return _data_dir() / "llm_secret.bin"


def _read_config() -> dict:
    p = _config_path()
    if not p.is_file():
        return {"version": 1}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {"version": 1}


def _write_config(data: dict) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _mask_key(k: str) -> str:
    """脱敏 API key —— 统一前 N + '...' + 后 N 格式（N 随长度自适应）。
    - 空：返回 ""
    - 长度 1~2：原样返回（无法脱敏）
    - 长度 3~4：前 1 + "..." + 后 1
    - 长度 5~8：前 2 + "..." + 后 2
    - 长度 ≥ 9：前 4 + "..." + 后 4
    """
    if not k:
        return ""
    n = len(k)
    if n <= 2:
        return k
    if n <= 4:
        head, tail = 1, 1
    elif n <= 8:
        head, tail = 2, 2
    else:
        head, tail = 4, 4
    return k[:head] + "..." + k[-tail:]


def get_config() -> dict:
    """读当前 LLM 配置（含脱敏 key）

    返回字段：
    - configured: bool — 是否可用（用户配置 OR 内置默认）
    - builtin: bool   — True 表示当前用的是内置默认（清除配置不会真清掉）
    - provider / model / base_url / key_masked
    """
    data = _read_config()
    llm = data.get("llm") or {}
    provider = llm.get("active_provider")
    # 兼容老 config（base_url / default_model 字段名保留作为历史记录）
    base_url = llm.get("base_url")  # 老字段，新逻辑不用
    model = llm.get("model") or llm.get("default_model")  # 新字段优先

    builtin_key = _builtin_api_key()

    # 1) 没用户配置 + 有内置默认 → 直接走内置默认
    if not provider:
        if builtin_key:
            try:
                preset = providers.get_by_id(DEFAULT_PROVIDER_ID)
                base = preset.get("base_url")
            except ValueError:
                base = None
            return {
                "configured": True,
                "builtin": True,
                "provider": DEFAULT_PROVIDER_ID,
                "model": DEFAULT_MODEL_ID,
                "base_url": base,
                "key_masked": _mask_key(builtin_key),
            }
        return {"configured": False, "provider": None, "model": None, "base_url": None}

    sp = _secret_path()
    if not sp.is_file():
        # 用户配置文件丢了 → 如果有内置默认就走 builtin（保留 config.yaml 里现有的
        # provider/model，仅标记 builtin=True），否则清掉 provider/model
        if builtin_key:
            try:
                preset = providers.get_by_id(provider)
                base = preset.get("base_url")
            except ValueError:
                base = None
            return {
                "configured": True,
                "builtin": True,
                "provider": provider,
                "model": model,
                "base_url": base,
                "key_masked": _mask_key(builtin_key),
            }
        data["llm"] = {"active_provider": None, "model": None}
        _write_config(data)
        return {"configured": False, "provider": None, "model": None, "base_url": None}

    try:
        raw_key = dpapi.unprotect_from_file(str(sp))
    except FileNotFoundError:
        # 文件竞态：上一步在时还在，现在没了 → 走 builtin 兜底（同上，保留现有 provider/model）
        if builtin_key:
            try:
                preset = providers.get_by_id(provider)
                base = preset.get("base_url")
            except ValueError:
                base = None
            return {
                "configured": True,
                "builtin": True,
                "provider": provider,
                "model": model,
                "base_url": base,
                "key_masked": _mask_key(builtin_key),
            }
        data["llm"] = {"active_provider": None, "model": None}
        _write_config(data)
        return {"configured": False, "provider": None, "model": None, "base_url": None}
    except Exception as e:
        # DPAPI 解密失败（密钥错 / 系统凭据变更 / 文件损坏）。
        # 不要静默删密钥 —— 用户可能想恢复。改成：
        # 1) 把当前密钥文件备份为 .bak（保留线索）
        # 2) 不删原文件
        # 3) 仍返回未配置（让 LLM 调用直接报"未配置"，用户能去 modal 重新配）
        import logging
        logging.getLogger("llm_config").warning(
            "DPAPI unprotect 失败，保留原文件作为 .bak，请重新配置 LLM: %s", e
        )
        try:
            bak = sp.with_suffix(sp.suffix + ".bak")
            sp.replace(bak)
        except OSError:
            pass
        return {
            "configured": False,
            "provider": provider,  # 保留原 provider 提示
            "model": model,
            "base_url": base_url,
            "warning": f"DPAPI 解密失败：{e}。原密钥已备份为 .bak，请重新配置 API key。",
        }

    return {
        "configured": True,
        "builtin": False,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "key_masked": _mask_key(raw_key),
    }


def set_config(provider: str, model: str, api_key: str, skip_key: bool = False) -> None:
    """保存 LLM 配置 + 加密 key 到 secret 文件
    - provider: 厂商 id
    - model:    模型 id（必须是 preset.models 里有的）
    - api_key:  加密后存 llm_secret.bin
              若为空：仅当 provider 是内置默认（aliyun_dashscope）时允许，
              此时不写 secret 文件，运行时用环境变量里的内置 key。
    - skip_key: True → 不动 secret 文件，只更新 config.yaml 的 provider/model
                （用于"用户没改 key"场景，避免把 masked 占位符存回去）
    """
    try:
        preset = providers.get_by_id(provider)
    except ValueError:
        raise ValueError(f"未知 provider id: {provider!r}")

    # 校验 model 必须是 preset 里的合法 id（防 typo / 旧配置残留）
    valid_ids = {m["id"] for m in preset.get("models") or []}
    if model not in valid_ids:
        raise ValueError(
            f"模型 {model!r} 不在 provider {provider!r} 的预设列表里（可选：{', '.join(sorted(valid_ids))}）"
        )

    if skip_key:
        # 只换厂商/模型，保留原 secret 文件不变
        data = _read_config()
        data["llm"] = {
            "active_provider": provider,
            "model": model,
        }
        _write_config(data)
        return

    if not api_key:
        # 空 key → 只允许内置默认 provider
        if provider != DEFAULT_PROVIDER_ID or not _builtin_api_key():
            raise ValueError("api_key 必填")
        # 删掉用户之前可能保存的 secret 文件，回到内置默认
        sp = _secret_path()
        if sp.is_file():
            try:
                sp.unlink()
            except OSError:
                pass
        data = _read_config()
        data["llm"] = {"active_provider": provider, "model": model}
        _write_config(data)
        return

    sp = _secret_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    dpapi.protect_to_file(api_key, str(sp))

    data = _read_config()
    data["llm"] = {
        "active_provider": provider,
        "model": model,
    }
    _write_config(data)


def clear_config() -> None:
    """清除用户配置（删除 secret 文件 + 把 config.yaml 的 llm 段重置为内置默认）

    内置默认 key（环境变量 DINGTALK_BOX_DEFAULT_QWEN_KEY）不会被清除，
    清除后 get_config() 仍会返回 builtin=True 的内置默认配置。
    """
    sp = _secret_path()
    if sp.is_file():
        try:
            sp.unlink()
        except OSError:
            pass
    data = _read_config()
    if _builtin_api_key():
        # 有内置默认 → 重置为内置默认（用户点「清除配置」后立刻还能用）
        data["llm"] = {
            "active_provider": DEFAULT_PROVIDER_ID,
            "model": DEFAULT_MODEL_ID,
        }
    else:
        # 没有内置默认（环境变量没配） → 真正未配置
        data["llm"] = {"active_provider": None, "model": None}
    _write_config(data)


def list_providers() -> list[dict]:
    """返回 10 个 preset（前端填充下拉框用）"""
    return providers.load_providers()
