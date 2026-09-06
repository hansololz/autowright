"""Backend entry point (§3/§4.9): the access-log developerMode filter."""
import logging


def _record(args):
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                             "%s", args, None)


def test_devmode_filter_scrubs_ws_token(home):
    # §4.9: the WS handshake carries the sole credential in the query string —
    # the access log must never copy it into backend.out.log.
    from autowright.main import _DevModeFilter

    f = _DevModeFilter()
    rec = _record(("GET /ws?token=abc123", 200, None))
    f.filter(rec)
    assert rec.args[0] == "GET /ws?token=***"  # value scrubbed, path kept
    assert rec.args[1:] == (200, None)  # non-str args untouched

    # a token mid-query keeps the surrounding parameters
    rec = _record(("GET /ws?a=1&token=s3cret&b=2",))
    f.filter(rec)
    assert rec.args == ("GET /ws?a=1&token=***&b=2",)


def test_devmode_filter_gates_info_lines(home):
    # §4.9: INFO request lines pass only while developerMode is on; WARNING+ always.
    from autowright.main import _DevModeFilter
    from autowright.storage import store

    store.load_all()
    f = _DevModeFilter()
    info = _record(("GET /state",))
    warn = logging.LogRecord("uvicorn.error", logging.WARNING, __file__, 1,
                             "%s", ("boom",), None)
    store.settings["developerMode"] = False
    assert f.filter(info) is False
    assert f.filter(warn) is True
    store.settings["developerMode"] = True
    assert f.filter(info) is True
    store.settings.pop("developerMode", None)  # the store is a module singleton


def test_boot_reconciles_keep_awake_from_settings(home, monkeypatch):
    # §3/§4.9: main() reconciles the permanent keepAwake assertion at boot with
    # the persisted setting's value, through the §2 platform layer.
    # Server/scheduler/listeners are stubbed — only the boot sequence up to
    # (and past) the reconcile call runs.
    import dataclasses

    from autowright import main as main_mod
    from autowright import paths
    from autowright import platform as platmod
    from autowright.yamlio import save_yaml

    save_yaml(paths.settings_file(), {"keepAwake": False})  # non-default value

    calls = []

    class RecordingPower:
        def reconcile(self, enabled: bool) -> None:
            calls.append(enabled)

        def hold_execution(self):
            return lambda: None

    fake = dataclasses.replace(platmod.current(), power=RecordingPower())
    monkeypatch.setattr(platmod, "current", lambda: fake)

    class _Stub:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    class _Server:
        def __init__(self, config):
            pass

        def run(self, sockets=None):
            pass

    monkeypatch.setattr(main_mod, "Scheduler", _Stub)
    monkeypatch.setattr(main_mod, "Listeners", _Stub)
    monkeypatch.setattr(main_mod.uvicorn, "Server", _Server)
    try:
        main_mod.main()
    finally:
        # main() attaches its developerMode filter to the live root handlers — strip
        # it so later tests' log capture isn't gated by this suite's settings.
        for hnd in logging.getLogger().handlers:
            for f in list(hnd.filters):
                if isinstance(f, main_mod._DevModeFilter):
                    hnd.removeFilter(f)
    assert calls == [False]  # the setting's value, not the default


def test_boot_time_boxes_the_graceful_shutdown(home, monkeypatch):
    # §3 quit-entirely: the renderer holds /ws open across quit-all and reset,
    # and an unbounded uvicorn shutdown waits on that socket forever — past the
    # service stop's own deregistration wait. Same boot harness as above; only
    # the server config is captured.
    from autowright import main as main_mod

    captured = {}

    class _Config:
        def __init__(self, app, **kwargs):
            captured.update(kwargs)

    class _Stub:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    class _Server:
        def __init__(self, config):
            pass

        def run(self, sockets=None):
            pass

    monkeypatch.setattr(main_mod, "Scheduler", _Stub)
    monkeypatch.setattr(main_mod, "Listeners", _Stub)
    monkeypatch.setattr(main_mod.uvicorn, "Config", _Config)
    monkeypatch.setattr(main_mod.uvicorn, "Server", _Server)
    try:
        main_mod.main()
    finally:
        for hnd in logging.getLogger().handlers:
            for f in list(hnd.filters):
                if isinstance(f, main_mod._DevModeFilter):
                    hnd.removeFilter(f)
    assert captured["timeout_graceful_shutdown"] == main_mod.SHUTDOWN_GRACE_S == 5


def test_lifespan_runs_registered_shutdown_callbacks(home):
    # §3: uvicorn re-raises the captured SIGTERM once run() returns, so code
    # after run() never executes on a signal-driven stop — every piece of
    # shutdown work must run from the api lifespan instead. Error-tolerant:
    # a failing callback must not keep the next one from running.
    from fastapi.testclient import TestClient

    from autowright import api
    from autowright.storage import store

    store.load_all()
    ran = []

    def broken():
        raise RuntimeError("boom")

    api.register_shutdown(broken)
    api.register_shutdown(lambda: ran.append("cleanup"))
    with TestClient(api.app):
        pass
    assert ran == ["cleanup"]


def test_sigterm_stop_unlinks_backend_json(tmp_path):
    # §3: the end-to-end shape of a service stop — a real backend process,
    # SIGTERMed the way launchd's bootout does it, exits inside the graceful
    # bound and removes its own backend.json (the lifespan-registered cleanup;
    # the process dies before any code after Server.run()).
    import os
    import signal
    import subprocess
    import sys
    import time

    env = {**os.environ, "AUTOWRIGHT_HOME": str(tmp_path), "AUTOWRIGHT_PORT": "0"}
    proc = subprocess.Popen([sys.executable, "-m", "autowright.main"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    backend_json = tmp_path / "backend.json"
    try:
        deadline = time.monotonic() + 20
        while not backend_json.exists():
            assert proc.poll() is None, "backend died before publishing backend.json"
            assert time.monotonic() < deadline, "backend never published backend.json"
            time.sleep(0.1)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=15)
        assert not backend_json.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_lifespan_quiesces_before_the_kill_sweep(home, monkeypatch):
    # §3 shutdown order: the work sources (scheduler, listeners) stop BEFORE
    # anything is killed — a tick landing after kill_all_live would start an
    # execution with nobody left to collect it. The registered shutdown
    # callbacks come last, after both kill sweeps.
    from fastapi.testclient import TestClient

    from autowright import api
    from autowright.storage import store

    store.load_all()
    order = []
    monkeypatch.setattr(api.engine, "kill_all_live", lambda: order.append("kill_live"))
    monkeypatch.setattr(api.draft_jobs, "kill_all_building",
                        lambda: order.append("kill_building"))

    def broken():
        raise RuntimeError("boom")

    api.register_quiesce(broken)  # error-tolerant, like the shutdown half
    api.register_quiesce(lambda: order.append("quiesce"))
    api.register_shutdown(lambda: order.append("shutdown"))
    with TestClient(api.app):
        pass
    assert order == ["quiesce", "kill_live", "kill_building", "shutdown"]


def test_main_shutdown_halves_each_run_once(home, monkeypatch):
    # §3: main() splits its cleanup into the quiesce half (scheduler +
    # listeners, registered on the lifespan's pre-kill hook) and the shutdown
    # half (stop flag + backend.json unlink). Both are run-once, and shutdown
    # calls quiesce defensively for a run() that never reached the lifespan.
    from autowright import api, main as main_mod

    stopped = []

    class FakeStopper:
        def __init__(self, name):
            self.name = name

        def start(self):
            pass

        def stop(self):
            stopped.append(self.name)

    monkeypatch.setattr(main_mod, "Scheduler", lambda *a, **kw: FakeStopper("scheduler"))
    monkeypatch.setattr(main_mod, "Listeners", lambda *a, **kw: FakeStopper("listeners"))
    monkeypatch.setattr(main_mod.uvicorn, "Server",
                        lambda config: type("S", (), {"run": lambda self, sockets=None: None})())
    try:
        main_mod.main()
    finally:
        # main() installs the §4.9 access-log filter on the root logger — drop
        # it again so it can't gate later tests' logging.
        for hnd in logging.getLogger().handlers:
            for f in list(hnd.filters):
                if isinstance(f, main_mod._DevModeFilter):
                    hnd.removeFilter(f)
    # the `finally` half ran both, in order, exactly once each
    assert stopped == ["scheduler", "listeners"]
    # ...and re-running the registered callbacks changes nothing
    for callback in api._quiesce_callbacks + api._shutdown_callbacks:
        callback()
    assert stopped == ["scheduler", "listeners"]
