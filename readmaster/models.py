"""Named model connections. API secrets are bound to the Windows user by DPAPI."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import threading
import urllib.parse
import uuid


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]


def secret_transform(raw, decrypt=False):
    if os.name != 'nt':
        raise ValueError('此客户端使用 Windows 加密保存密钥，请在 Windows 上配置 API')
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    memory = ctypes.create_string_buffer(raw)
    incoming = Blob(len(raw), ctypes.cast(memory, ctypes.POINTER(ctypes.c_byte)))
    outgoing = Blob()
    action = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    action.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                      ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    action.restype = wintypes.BOOL
    if not action(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ValueError('无法读取或保存密钥，请在当前 Windows 账号下重新填写 API Key')
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(outgoing.data)


def encrypt_key(key):
    return base64.b64encode(secret_transform(key.encode())).decode('ascii')


def decrypt_key(value):
    try:
        return secret_transform(base64.b64decode(value, validate=True), True).decode()
    except (ValueError, UnicodeError):
        raise ValueError('密钥无法解密，请在这台电脑的当前 Windows 账号下重新填写') from None


def connection(body):
    provider = body.get('provider', 'api')
    if provider not in ('api', 'codex'):
        raise ValueError('请选择 API 或 Codex')
    name = str(body.get('name', '')).strip()
    model = str(body.get('model', '')).strip()
    if not name or len(name) > 80:
        raise ValueError('配置名称不能为空，且不能超过 80 个字符')
    if len(model) > 120:
        raise ValueError('模型名称过长')
    try:
        revisions = int(body.get('max_revisions', 2))
    except (ValueError, TypeError):
        raise ValueError('修订次数应为 0–5') from None
    if not 0 <= revisions <= 5:
        raise ValueError('修订次数应为 0–5')
    endpoint = str(body.get('endpoint', 'https://api.openai.com/v1')).strip().rstrip('/')
    if provider == 'api':
        if not model:
            raise ValueError('请填写服务商提供的模型名称')
        url = urllib.parse.urlsplit(endpoint)
        if (not url.hostname or url.username or url.password or url.query or url.fragment
                or any(c.isspace() for c in endpoint) or len(endpoint) > 300
                or not (url.scheme == 'https' or (url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost')))):
            raise ValueError('API 地址须为 HTTPS，或 localhost / 127.0.0.1 的本机 HTTP 地址')
        if endpoint.endswith('/chat/completions'):
            endpoint = endpoint[:-len('/chat/completions')]
    return {'name': name, 'provider': provider, 'model': model, 'endpoint': endpoint,
            'max_revisions': revisions}


class ModelStore:
    def __init__(self, directory, legacy=None, existing_library=False):
        self.path = Path(directory) / 'models.json'
        self.lock = threading.RLock()
        if self.path.exists():
            try:
                self.state = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(self.state.get('profiles'), list):
                    raise ValueError()
            except (ValueError, AttributeError):
                raise ValueError('Models 配置文件无法读取，请保留 data/models.json 并检查文件') from None
        else:
            builtin = {'id': 'codex', 'name': 'Codex', 'provider': 'codex', 'model': '',
                       'endpoint': 'https://api.openai.com/v1', 'max_revisions': 2}
            profiles = [builtin]
            active = None
            if legacy:
                profile = {**builtin, **{k: legacy[k] for k in builtin if k in legacy}}
                profile['id'] = 'codex' if profile['provider'] == 'codex' else uuid.uuid4().hex
                profile['name'] = 'Codex' if profile['provider'] == 'codex' else '已有 API 配置'
                if profile['provider'] == 'codex':
                    profiles[0] = profile
                else:
                    profiles.append(profile)
                active = profile['id']
            elif existing_library:
                active = 'codex'
            self.state = {'version': 1, 'profiles': profiles, 'active_id': active,
                          'onboarded': bool(legacy or existing_library)}
            self.persist()

    def persist(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def find(self, ident):
        result = next((p for p in self.state['profiles'] if p['id'] == ident), None)
        if result is None:
            raise ValueError('模型配置不存在，请刷新 Models')
        return result

    def public(self):
        with self.lock:
            result = {'active_id': self.state['active_id'], 'onboarded': self.state['onboarded'], 'profiles': []}
            for profile in self.state['profiles']:
                public = {k: profile.get(k) for k in ('id', 'name', 'provider', 'model', 'endpoint', 'max_revisions')}
                public['has_key'] = bool(profile.get('encrypted_key'))
                public['key_mask'] = '••••••••' + profile.get('key_suffix', '') if public['has_key'] else ''
                result['profiles'].append(public)
            return result

    def save(self, body):
        with self.lock:
            ident = body.get('id')
            previous = self.find(ident) if ident else None
            validated = connection(body)
            if ident == 'codex' and validated['provider'] != 'codex':
                raise ValueError('默认 Codex 配置不能改成 API，请新建 API 配置')
            key = str(body.get('api_key', '')).strip()
            if len(key) > 8000 or '\n' in key or '\r' in key:
                raise ValueError('API Key 格式无效')
            profile = {**validated, 'id': ident or uuid.uuid4().hex}
            if profile['provider'] == 'api':
                if key:
                    profile.update(encrypted_key=encrypt_key(key), key_suffix=key[-4:] if len(key) > 8 else '')
                elif previous and previous.get('encrypted_key'):
                    profile.update(encrypted_key=previous['encrypted_key'], key_suffix=previous.get('key_suffix', ''))
                else:
                    raise ValueError('请输入 API Key')
            if previous:
                self.state['profiles'][self.state['profiles'].index(previous)] = profile
            else:
                self.state['profiles'].append(profile)
            self.persist()
            return profile['id']

    def snapshot(self, ident=None):
        with self.lock:
            profile = self.find(ident or self.state['active_id'])
            config = {k: profile[k] for k in ('provider', 'model', 'endpoint', 'max_revisions')}
            config['profile_id'] = profile['id']
            key = decrypt_key(profile['encrypted_key']) if profile.get('encrypted_key') else ''
            return config, key

    def activate(self, ident):
        with self.lock:
            config, key = self.snapshot(ident)
            if config['provider'] == 'api' and not key:
                raise ValueError('请先编辑这套配置并填写 API Key')
            self.state['active_id'] = ident
            self.state['onboarded'] = True
            self.persist()
            return config, key

    def delete(self, ident):
        with self.lock:
            if ident == 'codex':
                raise ValueError('默认 Codex 入口保留，可新建其他模型配置')
            profile = self.find(ident)
            self.state['profiles'].remove(profile)
            if self.state['active_id'] == ident:
                self.state['active_id'] = None
            self.persist()

    def skip_onboarding(self):
        with self.lock:
            self.state['onboarded'] = True
            self.persist()
