"""Shared application assembly; each transport explicitly chooses native execution."""
from forge.application.harness_adapter import LocalTrustedBackend
from forge.application.services import ApplicationServices
from forge.engine.test_profile import MemoryCredentials
from forge.sandbox.application_backend import NativeBackendFactory


def create_application(store, *, profile, mode, native_execution, profile_id=None, scripted=None,
                       interactive_approvals=False, observation_options=None, evaluation_adapter=None):
    credentials = scripted.credentials if scripted else MemoryCredentials()
    service = ApplicationServices(store, profile_id=profile_id or profile + '-profile', credentials=credentials,
        mode=mode, backend=LocalTrustedBackend() if mode == 'local-trusted' else None,
        backend_factory=NativeBackendFactory(store.data_dir, mode=mode) if native_execution and mode != 'local-trusted' else None,
        model_client_factory=scripted.model_client_factory if scripted else None,
        task_relation='new' if scripted else None,
        approval_handler=scripted.approval_handler if scripted and not interactive_approvals else None,
        observation_options=observation_options)
    if profile != 'test':
        from benchmark.adapters.harbor import HarborExecutor
        service.evaluation_executor = HarborExecutor(service, adapter=evaluation_adapter)
    return service
