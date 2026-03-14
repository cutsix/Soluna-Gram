import contextlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ValidationError

import solgram.modules
from solgram import Config, logs
from solgram.enums import Message
from solgram.common.cache import cache
from solgram.utils import client
from solgram.services import sqlite

plugins_path = Path("plugins")


class PrivateRepoAuthError(Exception):
    def __init__(self, error_detail: dict, status_code: Optional[int] = None):
        if not isinstance(error_detail, dict):
            error_detail = {}
        self.error_detail = error_detail
        self.status_code = status_code
        super().__init__(error_detail.get("error", "auth_error"))


class PluginMeta(BaseModel):
    version: Optional[float] = None
    remote_url: str = ""
    auth_type: str = "public"


def _build_auth_error(response) -> PrivateRepoAuthError:
    try:
        error_detail = response.json()
    except ValueError:
        error_detail = {}
    if not isinstance(error_detail, dict):
        error_detail = {}
    error_detail.setdefault("error", "auth_error")
    return PrivateRepoAuthError(error_detail, status_code=response.status_code)


class LocalPlugin(BaseModel):
    name: str
    status: bool
    installed: bool = False
    version: Optional[float] = None

    @property
    def normal_path(self) -> Path:
        return plugins_path / f"{self.name}.py"

    @property
    def disabled_path(self) -> Path:
        return plugins_path / f"{self.name}.py.disabled"

    @property
    def load_status(self) -> bool:
        """插件加载状态"""
        active_plugins = solgram.modules.plugin_list
        return self.name in active_plugins

    def remove(self):
        with contextlib.suppress(FileNotFoundError):
            os.remove(self.normal_path)
        with contextlib.suppress(FileNotFoundError):
            os.remove(self.disabled_path)

    def enable(self) -> bool:
        try:
            os.rename(self.disabled_path, self.normal_path)
            return True
        except Exception:
            return False

    def disable(self) -> bool:
        try:
            os.rename(self.normal_path, self.disabled_path)
            return True
        except Exception:
            return False


class RemotePlugin(LocalPlugin):
    section: str
    maintainer: str
    size: str
    supported: bool
    des: str = ""
    des_short: str = ""
    remote_source: str
    auth_type: str = "public"

    async def install(self, token: Optional[str] = None) -> bool:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        html = await client.get(f"{self.remote_source}{self.name}/main.py", headers=headers)
        if html.status_code in (401, 403):
            raise _build_auth_error(html)
        if html.status_code == 200:
            self.remove()
            with open(plugins_path / f"{self.name}.py", mode="wb") as f:
                f.write(html.text.encode("utf-8"))
            return True
        return False


class PluginRemote(BaseModel):
    url: str
    status: bool
    auth_type: str = "public"
    token: Optional[str] = None

    @property
    def text(self) -> str:
        prefix = "🔒 " if self.auth_type == "private" else ""
        return f"{'✅' if self.status else '❌'} {prefix}{self.url}"


class PluginRemoteManager:
    def __init__(self):
        self.key = "plugins_remotes"

    def get_remotes(self) -> List[PluginRemote]:
        return [PluginRemote(**i) for i in sqlite.get(self.key, [])]

    def set_remotes(self, remotes: List[PluginRemote]):
        sqlite[self.key] = [i.dict() for i in remotes]

    def get_remote(self, remote_url: str) -> Optional[PluginRemote]:
        return next(filter(lambda x: x.url == remote_url, self.get_remotes()), None)

    def add_remote(self, remote_url: str) -> bool:
        remotes = self.get_remotes()
        if not next(filter(lambda x: x.url == remote_url, remotes), None):
            remotes.append(PluginRemote(url=remote_url, status=True))
            self.set_remotes(remotes)
            return True
        return False

    def add_private_remote(self, remote_url: str, token: str) -> bool:
        remotes = self.get_remotes()
        existing = next(filter(lambda x: x.url == remote_url, remotes), None)
        if existing:
            existing.token = token
            existing.auth_type = "private"
            existing.status = True
        else:
            remotes.append(
                PluginRemote(
                    url=remote_url,
                    status=True,
                    auth_type="private",
                    token=token,
                )
            )
        self.set_remotes(remotes)
        return True

    def update_token(self, remote_url: str, token: str) -> bool:
        remotes = self.get_remotes()
        if remote := next(filter(lambda x: x.url == remote_url, remotes), None):
            remote.token = token
            remote.auth_type = "private"
            remote.status = True
            self.set_remotes(remotes)
            return True
        return False

    def remove_remote(self, remote_url: str) -> bool:
        remotes = self.get_remotes()
        if next(filter(lambda x: x.url == remote_url, remotes), None):
            remotes = [i for i in remotes if i.url != remote_url]
            self.set_remotes(remotes)
            return True
        return False

    def disable_remote(self, remote_url: str) -> bool:
        remotes = self.get_remotes()
        if remote := next(filter(lambda x: x.url == remote_url, remotes), None):
            remote.status = False
            self.set_remotes(remotes)
            return True
        return False

    def enable_remote(self, remote_url: str) -> bool:
        remotes = self.get_remotes()
        if remote := next(filter(lambda x: x.url == remote_url, remotes), None):
            remote.status = True
            self.set_remotes(remotes)
            return True
        return False


class PluginManager:
    def __init__(self, remote_manager: PluginRemoteManager):
        self.remote_manager = remote_manager
        self.version_map: Dict[str, Dict[str, Any]] = {}
        self.remote_version_map = {}
        self.plugins: List[LocalPlugin] = []
        self.remote_plugins: List[RemotePlugin] = []
        self.remote_plugins_all: List[RemotePlugin] = []
        self._auth_errors: Dict[str, Dict[str, Any]] = {}

    def load_local_version_map(self):
        self.version_map = {}
        if not os.path.exists(plugins_path / "version.json"):
            return
        with open(plugins_path / "version.json", "r", encoding="utf-8") as f:
            raw_map = json.load(f)
        if not isinstance(raw_map, dict):
            return
        for name, data in raw_map.items():
            if isinstance(data, (float, int)):
                meta = PluginMeta(version=float(data))
            elif isinstance(data, dict):
                try:
                    version = data.get("version")
                    meta = PluginMeta(
                        version=float(version) if version is not None else None,
                        remote_url=str(data.get("remote_url", "") or ""),
                        auth_type=str(data.get("auth_type", "public") or "public"),
                    )
                except (TypeError, ValueError):
                    logs.warning(f"本地插件 {name} 版本信息无效，已跳过")
                    continue
            else:
                logs.warning(f"本地插件 {name} 版本信息格式无效，已跳过")
                continue
            self.version_map[name] = meta.dict()

    def save_local_version_map(self):
        with open(plugins_path / "version.json", "w", encoding="utf-8") as f:
            json.dump(self.version_map, f, indent=4)

    def ensure_local_version_map_loaded(self):
        if not self.version_map and os.path.exists(plugins_path / "version.json"):
            self.load_local_version_map()

    def get_local_plugin_meta(self, name: str) -> Optional[Dict[str, Any]]:
        self.ensure_local_version_map_loaded()
        data = self.version_map.get(name)
        return dict(data) if data else None

    def get_local_version(self, name: str) -> Optional[float]:
        data = self.get_local_plugin_meta(name)
        if data is None:
            return None
        version = data.get("version")
        return float(version) if version is not None else None

    def set_local_plugin_meta(
        self, name: str, version: Optional[float], remote_url: str, auth_type: str
    ) -> None:
        self.ensure_local_version_map_loaded()
        self.version_map[name] = PluginMeta(
            version=version,
            remote_url=remote_url,
            auth_type=auth_type,
        ).dict()
        self.save_local_version_map()

    def get_local_plugin_source(self, name: str) -> Optional[Dict[str, str]]:
        data = self.get_local_plugin_meta(name)
        if data is None:
            return None
        return {
            "remote_url": data.get("remote_url", ""),
            "auth_type": data.get("auth_type", "public"),
        }

    def get_plugin_install_status(self, name: str) -> bool:
        self.ensure_local_version_map_loaded()
        return name in self.version_map

    @staticmethod
    def get_plugin_load_status(name: str) -> bool:
        return bool(os.path.exists(plugins_path / f"{name}.py"))

    def remove_plugin(self, name: str) -> bool:
        self.ensure_local_version_map_loaded()
        if plugin := self.get_local_plugin(name):
            plugin.remove()
        removed = False
        if name in self.version_map:
            self.version_map.pop(name)
            self.save_local_version_map()
            removed = True
        return bool(plugin) or removed

    def enable_plugin(self, name: str) -> bool:
        if plugin := self.get_local_plugin(name):
            return plugin.enable()
        return False

    def disable_plugin(self, name: str) -> bool:
        if plugin := self.get_local_plugin(name):
            return plugin.disable()
        return False

    def load_local_plugins(self) -> List[LocalPlugin]:
        self.load_local_version_map()
        self.plugins = []
        for plugin in os.listdir("plugins"):
            if plugin.endswith(".py") or plugin.endswith(".py.disabled"):
                plugin = (
                    plugin[:-12] if plugin.endswith(".py.disabled") else plugin[:-3]
                )
                self.plugins.append(
                    LocalPlugin(
                        name=plugin,
                        installed=self.get_plugin_install_status(plugin),
                        status=self.get_plugin_load_status(plugin),
                        version=self.get_local_version(plugin),
                    )
                )
        return self.plugins

    def get_local_plugin(self, name: str) -> LocalPlugin:
        if plugin := next(filter(lambda x: x.name == name, self.plugins), None):
            return plugin
        normal_path = plugins_path / f"{name}.py"
        disabled_path = plugins_path / f"{name}.py.disabled"
        if not normal_path.exists() and not disabled_path.exists():
            return None
        return LocalPlugin(
            name=name,
            installed=self.get_plugin_install_status(name),
            status=self.get_plugin_load_status(name),
            version=self.get_local_version(name),
        )

    @staticmethod
    async def fetch_remote_url(url: str, token: Optional[str] = None) -> List[Dict]:
        try:
            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            data = await client.get(f"{url}list.json", headers=headers)
            if data.status_code in (401, 403):
                raise _build_auth_error(data)
            data.raise_for_status()
            return data.json()["list"]
        except Exception as e:
            logs.error(f"获取远程插件列表失败: {e}")
            raise e

    async def load_remote_plugins_no_cache(self) -> List[RemotePlugin]:
        self._auth_errors = {}
        remotes = self.remote_manager.get_remotes()
        ordered_remotes = sorted(
            remotes, key=lambda remote: 0 if remote.auth_type == "private" else 1
        )
        all_sources = [
            (
                remote.url,
                remote.token if remote.auth_type == "private" else None,
                remote,
            )
            for remote in ordered_remotes
        ]
        if Config.GIT_SOURCE:
            all_sources.append((Config.GIT_SOURCE, None, None))
        plugins = []
        all_plugins = []
        plugins_name = []
        for url, token, remote_obj in all_sources:
            try:
                plugin_list = await self.fetch_remote_url(url, token=token)
            except PrivateRepoAuthError as e:
                self._auth_errors[url] = e.error_detail
                continue
            except Exception as e:
                logs.error(f"获取远程插件列表失败: {e}")
                if remote_obj:
                    self.remote_manager.disable_remote(url)
                continue
            if remote_obj:
                self.remote_manager.enable_remote(url)
            auth_type = remote_obj.auth_type if remote_obj else "public"
            for plugin in plugin_list:
                try:
                    plugin_model = RemotePlugin(
                        **plugin,
                        status=False,
                        remote_source=url,
                        auth_type=auth_type,
                    )
                    all_plugins.append(plugin_model)
                    if plugin_model.name in plugins_name:
                        continue
                    plugins.append(plugin_model)
                    plugins_name.append(plugin_model.name)
                except ValidationError:
                    logs.warning(f"远程插件 {plugin} 信息不完整")
                    continue
        self.remote_plugins = plugins
        self.remote_plugins_all = all_plugins
        self.remote_version_map = {plugin.name: plugin.version for plugin in plugins}
        return plugins

    @cache()
    async def load_remote_plugins_cache(self) -> List[RemotePlugin]:
        return await self.load_remote_plugins_no_cache()

    async def load_remote_plugins(
        self, enable_cache: bool = True
    ) -> List[RemotePlugin]:
        has_private = any(
            remote.auth_type == "private" for remote in self.remote_manager.get_remotes()
        )
        if has_private:
            enable_cache = False
        plugin_list = (
            await self.load_remote_plugins_cache()
            if enable_cache
            else await self.load_remote_plugins_no_cache()
        )
        for i in plugin_list:
            i.status = self.get_plugin_load_status(i.name)
        return plugin_list

    def get_auth_errors(self, urls: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        if urls is None:
            return list(self._auth_errors.values())
        return [
            error
            for url, error in self._auth_errors.items()
            if url in urls
        ]

    def get_remote_plugin(
        self,
        name: str,
        remote_source: Optional[str] = None,
        auth_type: Optional[str] = None,
        include_all: bool = False,
    ) -> Optional[RemotePlugin]:
        plugins = self.remote_plugins_all if (include_all or remote_source) else self.remote_plugins
        for plugin in plugins:
            if plugin.name != name:
                continue
            if remote_source and plugin.remote_source != remote_source:
                continue
            if auth_type and plugin.auth_type != auth_type:
                continue
            return plugin
        return None

    def get_update_target_plugin(self, name: str) -> Optional[RemotePlugin]:
        source_info = self.get_local_plugin_source(name)
        if source_info is None:
            return None
        remote_url = source_info.get("remote_url", "")
        auth_type = source_info.get("auth_type", "public")
        if auth_type == "public" and not remote_url:
            return next(
                (
                    plugin
                    for plugin in self.remote_plugins_all
                    if plugin.name == name and plugin.auth_type == "public"
                ),
                None,
            )
        return self.get_remote_plugin(
            name,
            remote_source=remote_url,
            auth_type=auth_type,
            include_all=True,
        )

    def plugin_need_update(self, name: str) -> bool:
        local_version = self.get_local_version(name)
        if local_version is None or local_version == 0.0:
            return False
        remote_plugin = self.get_update_target_plugin(name)
        if remote_plugin and remote_plugin.version is not None:
            return local_version < remote_plugin.version
        return False

    async def install_remote_plugin(
        self, name: str, remote_source: Optional[str] = None
    ) -> bool:
        plugin = self.get_remote_plugin(
            name,
            remote_source=remote_source,
            include_all=remote_source is not None,
        )
        if plugin:
            remote = self.remote_manager.get_remote(plugin.remote_source)
            token = (
                remote.token if remote and remote.auth_type == "private" else None
            )
            try:
                if await plugin.install(token=token):
                    self.set_local_plugin_meta(
                        name,
                        plugin.version,
                        plugin.remote_source,
                        plugin.auth_type,
                    )
                    return True
            except PrivateRepoAuthError as e:
                self._auth_errors[plugin.remote_source] = e.error_detail
        return False

    async def update_remote_plugin(self, name: str) -> bool:
        if self.plugin_need_update(name):
            remote_plugin = self.get_update_target_plugin(name)
            if remote_plugin is None:
                return False
            return await self.install_remote_plugin(
                name, remote_source=remote_plugin.remote_source
            )
        return False

    async def update_all_remote_plugin(self) -> List[RemotePlugin]:
        updated_plugins = []
        self.ensure_local_version_map_loaded()
        for plugin_name in list(self.version_map.keys()):
            remote_plugin = self.get_update_target_plugin(plugin_name)
            if remote_plugin is None or not self.plugin_need_update(plugin_name):
                continue
            if await self.install_remote_plugin(
                plugin_name, remote_source=remote_plugin.remote_source
            ):
                updated_plugins.append(remote_plugin)
        return updated_plugins

    @staticmethod
    async def download_from_message(message: Message) -> str:
        """Download a plugin from a message"""
        reply = message.reply_to_message
        file_path = None
        if (
            reply
            and reply.document
            and reply.document.file_name
            and reply.document.file_name.endswith(".py")
        ):
            file_path = await message.reply_to_message.download()
        elif (
            message.document
            and message.document.file_name
            and message.document.file_name.endswith(".py")
        ):
            file_path = await message.download()
        return file_path

    def get_plugins_status(
        self,
    ) -> Tuple[List[str], List[LocalPlugin], List[LocalPlugin]]:
        """Get plugins status"""
        all_local_plugins = self.plugins
        active_plugins = solgram.modules.plugin_list
        disabled_plugins = []
        inactive_plugins = []
        for plugin in all_local_plugins:
            if not plugin.status:
                inactive_plugins.append(plugin)
            elif not plugin.load_status:
                disabled_plugins.append(plugin)
        return active_plugins, disabled_plugins, inactive_plugins


plugin_remote_manager = PluginRemoteManager()
plugin_manager = PluginManager(plugin_remote_manager)
