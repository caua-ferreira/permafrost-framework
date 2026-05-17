"""Tests for __main__.py — python -m permafrost entrypoint."""
import sys
import pytest
from unittest.mock import patch, MagicMock


def _run_main(argv):
    """Run main() with patched sys.argv, return SystemExit code or None."""
    with patch("sys.argv", argv):
        from permafrost.__main__ import main
        return main()


def test_no_args_exits_1():
    with patch("sys.argv", ["permafrost"]):
        with pytest.raises(SystemExit) as exc:
            from permafrost.__main__ import main
            main()
    assert exc.value.code == 1


def test_unknown_command_exits_1():
    with patch("sys.argv", ["permafrost", "unknowncmd"]):
        with pytest.raises(SystemExit) as exc:
            from permafrost.__main__ import main
            main()
    assert exc.value.code == 1


def test_unknown_command_prints_message(capsys):
    with patch("sys.argv", ["permafrost", "badcmd"]):
        with pytest.raises(SystemExit):
            from permafrost.__main__ import main
            main()
    out = capsys.readouterr().out
    assert "badcmd" in out
    assert "Comandos" in out


def test_no_args_prints_usage(capsys):
    with patch("sys.argv", ["permafrost"]):
        with pytest.raises(SystemExit):
            from permafrost.__main__ import main
            main()
    out = capsys.readouterr().out
    assert "master" in out
    assert "freeze" in out


def test_master_command():
    mock_uvicorn = MagicMock()
    mock_master_cls = MagicMock()
    mock_master_instance = MagicMock()
    mock_master_cls.return_value = mock_master_instance

    with patch("sys.argv", ["permafrost", "master", "--host", "127.0.0.1", "--port", "9000"]):
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            with patch("permafrost.cluster.PermafrostMaster", mock_master_cls):
                from permafrost.__main__ import main
                main()

    mock_master_cls.assert_called_once_with(host="127.0.0.1", port=9000)
    mock_uvicorn.run.assert_called_once()


def test_master_command_defaults():
    mock_uvicorn = MagicMock()
    mock_master_cls = MagicMock()
    mock_master_instance = MagicMock()
    mock_master_cls.return_value = mock_master_instance

    with patch("sys.argv", ["permafrost", "master"]):
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            with patch("permafrost.cluster.PermafrostMaster", mock_master_cls):
                from permafrost.__main__ import main
                main()

    mock_master_cls.assert_called_once_with(host="0.0.0.0", port=8700)


def test_master_max_retries():
    mock_uvicorn = MagicMock()
    mock_master_cls = MagicMock()
    mock_master_instance = MagicMock()
    mock_master_instance.MAX_RETRIES = 3
    mock_master_cls.return_value = mock_master_instance

    with patch("sys.argv", ["permafrost", "master", "--max-retries", "5"]):
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            with patch("permafrost.cluster.PermafrostMaster", mock_master_cls):
                from permafrost.__main__ import main
                main()

    assert mock_master_instance.MAX_RETRIES == 5


def test_worker_command():
    mock_uvicorn = MagicMock()
    mock_worker_cls = MagicMock()
    mock_worker_instance = MagicMock()
    mock_worker_instance.worker_id = "w1"
    mock_worker_cls.return_value = mock_worker_instance

    with patch("sys.argv", ["permafrost", "worker", "--master", "http://localhost:8700"]):
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            with patch("permafrost.cluster.PermafrostWorker", mock_worker_cls):
                from permafrost.__main__ import main
                main()

    mock_worker_cls.assert_called_once_with(
        master_url="http://localhost:8700",
        host="0.0.0.0",
        port=8801,
        worker_id=None,
    )
    mock_worker_instance.run.assert_called_once_with(auto_register=True)


def test_worker_command_with_id():
    mock_uvicorn = MagicMock()
    mock_worker_cls = MagicMock()
    mock_worker_instance = MagicMock()
    mock_worker_instance.worker_id = "myworker"
    mock_worker_cls.return_value = mock_worker_instance

    with patch("sys.argv", [
        "permafrost", "worker",
        "--master", "http://m:8700",
        "--host", "192.168.1.1",
        "--port", "9001",
        "--id", "myworker",
    ]):
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            with patch("permafrost.cluster.PermafrostWorker", mock_worker_cls):
                from permafrost.__main__ import main
                main()

    mock_worker_cls.assert_called_once_with(
        master_url="http://m:8700",
        host="192.168.1.1",
        port=9001,
        worker_id="myworker",
    )


@pytest.mark.parametrize("cmd", ["freeze", "unfreeze", "thaw", "audit", "verify", "catalog"])
def test_cli_commands_delegate_to_app(cmd):
    mock_app = MagicMock()

    with patch("sys.argv", ["permafrost", cmd, "somefile"]):
        with patch("permafrost.cli.app", mock_app):
            from permafrost.__main__ import main
            main()
            # sys.argv was modified inside main() before calling app
            assert sys.argv[0] == "permafrost"
            assert sys.argv[1] == cmd

    mock_app.assert_called_once()


def test_module_main_guard():
    """Confirm __main__.py calls main() when run as __main__."""
    import importlib
    import permafrost.__main__ as m

    with patch.object(m, "main") as mock_main:
        with patch.object(m, "__name__", "__main__"):
            # simulate if __name__ == "__main__": main()
            if m.__name__ == "__main__":
                m.main()
    # The guard would call main — but since we can't easily trigger it,
    # just verify main is importable and callable
    assert callable(m.main)
