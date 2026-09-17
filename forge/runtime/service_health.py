'''Bounded endpoint health shared by model, router and helper clients.'''

from dataclasses import dataclass
from hashlib import sha256
from time import monotonic
from weakref import WeakValueDictionary

from forge.runtime.model_client import ModelCallError


@dataclass
class ServiceHealth:
    threshold: int = 3
    cooldown_seconds: float = 60.0
    failures: int = 0
    opened_at: float | None = None
    probe_inflight: bool = False
    paused: bool = False

    def before_request(self, now=None):
        now = monotonic() if now is None else now
        if self.paused:
            raise ModelCallError('service_paused', 'Service recovery failed; resume explicitly after checking connectivity.', retryable=False)
        if self.opened_at is not None:
            # 冷却后只放行一个探测请求，避免多个客户端同时重试冲击故障服务。
            if now - self.opened_at < self.cooldown_seconds or self.probe_inflight:
                raise ModelCallError('service_circuit_open', 'Service requests are temporarily paused after repeated failures.', retryable=False)
            self.probe_inflight = True

    def failed(self, now=None):
        self.failures += 1
        if self.probe_inflight:
            self.paused = True
        if self.failures >= self.threshold:
            self.opened_at = monotonic() if now is None else now

    def succeeded(self):
        self.failures = 0
        self.opened_at = None
        self.probe_inflight = False
        self.paused = False


_profiles = WeakValueDictionary()
# 此健康状态只在当前进程内共享，不代表跨进程的评测批次熔断。


def profile_health(config):
    if config is None:
        return ServiceHealth()
    # Separate credentials and routes, without storing an API key in a registry.
    key = (config.provider, config.base_url, config.model_id,
           sha256(config.api_key.encode()).hexdigest())
    health = _profiles.get(key)
    if health is None:
        health = ServiceHealth()
        _profiles[key] = health
    return health
