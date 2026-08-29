"""cli：--example 可选值参数（裸 --example 默认 lorem）的 argv 规范化。"""

from quicksrt.cli import _normalize_example_argv


def test_bare_example_gets_lorem():
    assert _normalize_example_argv(["preview", "--example"]) == ["preview", "--example", "lorem"]


def test_bare_example_at_end():
    assert _normalize_example_argv(["preview", "--crop", "--example"]) == [
        "preview", "--crop", "--example", "lorem",
    ]


def test_bare_example_before_other_option():
    assert _normalize_example_argv(["preview", "--example", "--crop"]) == [
        "preview", "--example", "lorem", "--crop",
    ]


def test_example_with_value_kept():
    assert _normalize_example_argv(["preview", "--example", "glass"]) == [
        "preview", "--example", "glass",
    ]


def test_example_equals_form_kept():
    assert _normalize_example_argv(["preview", "--example=fox"]) == ["preview", "--example=fox"]


def test_other_subcommands_untouched():
    assert _normalize_example_argv(["download", "--example"]) == ["download", "--example"]


def test_global_option_before_subcommand():
    assert _normalize_example_argv(["--no-color", "preview", "--example"]) == [
        "--no-color", "preview", "--example", "lorem",
    ]


def test_no_subcommand_untouched():
    assert _normalize_example_argv(["--no-color"]) == ["--no-color"]


# ---------- preview 多预设/全预设 ----------


def _patch_run(monkeypatch, tmp_path):
    """mock preview_step.run，记录调用参数，返回假输出路径。"""
    from quicksrt.steps import preview as preview_step

    seen = []
    monkeypatch.setattr(
        preview_step, "run",
        lambda cfg, workdir, log, **kw: seen.append(kw) or tmp_path / "out.png",
    )
    return seen


def test_preview_preset_multi(tmp_path, monkeypatch, caplog):
    """--preset a,b：按逗号分隔逐个渲染，结果走标准日志（info 级）。"""
    import logging
    from typer.testing import CliRunner
    from quicksrt import cli

    seen = _patch_run(monkeypatch, tmp_path)
    with caplog.at_level(logging.INFO, logger="quicksrt"):
        res = CliRunner().invoke(cli.app, ["preview", "--example", "lorem", "--preset", "plex,plex_yellow"])
    assert res.exit_code == 0, res.output
    assert [kw["preset"] for kw in seen] == ["plex", "plex_yellow"]
    assert caplog.text.count("preview 完成:") == 2


def test_preview_all_preset(tmp_path, monkeypatch):
    """--all-preset：渲染 presets.toml 全部预设（按名排序）。"""
    from typer.testing import CliRunner
    from quicksrt import cli
    from quicksrt.config import load_config

    seen = _patch_run(monkeypatch, tmp_path)
    res = CliRunner().invoke(cli.app, ["preview", "--example", "lorem", "--all-preset"])
    assert res.exit_code == 0, res.output
    assert [kw["preset"] for kw in seen] == sorted(load_config("config.toml").presets)


def test_preview_preset_and_all_preset_conflict(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from quicksrt import cli

    _patch_run(monkeypatch, tmp_path)
    res = CliRunner().invoke(
        cli.app, ["preview", "--example", "lorem", "--preset", "plex", "--all-preset"]
    )
    assert res.exit_code != 0
    assert "互斥" in res.output


def test_preview_all_preset_display(tmp_path, monkeypatch):
    """--all-preset --display：iTerm2 兼容终端逐图展示；渲染到临时目录，运行后无残留文件。"""
    from typer.testing import CliRunner
    from quicksrt import cli
    from quicksrt.config import load_config

    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.delenv("TMUX", raising=False)
    seen = _patch_run(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli.preview_step, "inline_image_escape",
        lambda p, width="100%": f"[IMG:{width}]",
    )
    n = len(load_config("config.toml").presets)
    res = CliRunner().invoke(cli.app, ["preview", "--example", "lorem", "--all-preset", "--display"])
    assert res.exit_code == 0, res.output
    # 每张图 100% 宽度、转义序列后各有一个换行（一个一行）
    assert res.output.count("[IMG:100%]\n") == n
    # 预设名单独一行，图片在下一行（按名排序）
    for p in sorted(load_config("config.toml").presets):
        assert f"[{p}]\n[IMG:100%]\n" in res.output
    # --display 渲染到临时目录，展示后整体删除：不产生文件
    assert len(seen) == n
    for kw in seen:
        assert kw["out_dir"] is not None and kw["out_dir"].name.startswith("quicksrt-display-")
        assert not kw["out_dir"].exists()


def test_preview_display_incompatible_terminal(tmp_path, monkeypatch, caplog):
    """--display 在非 iTerm2 终端：warning 并直接退出，不渲染。"""
    import logging
    from typer.testing import CliRunner
    from quicksrt import cli

    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    seen = _patch_run(monkeypatch, tmp_path)
    with caplog.at_level(logging.WARNING, logger="quicksrt"):
        res = CliRunner().invoke(cli.app, ["preview", "--example", "lorem", "--display"])
    assert res.exit_code == 1
    assert not seen  # 未渲染
    assert "不是 iTerm2" in caplog.text and "--display 不可用" in caplog.text


def test_preview_display_incompatible_tmux(tmp_path, monkeypatch):
    """--display 在 tmux 会话中同样视为不兼容：warning 并退出，不渲染。"""
    import logging
    from typer.testing import CliRunner
    from quicksrt import cli

    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1000,0")
    seen = _patch_run(monkeypatch, tmp_path)
    res = CliRunner().invoke(cli.app, ["preview", "--example", "lorem", "--display"])
    assert res.exit_code == 1
    assert not seen


def test_preview_display_legacy_alias(tmp_path, monkeypatch):
    """--inline-image 作为 --display 的兼容别名仍可用。"""
    from typer.testing import CliRunner
    from quicksrt import cli

    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.delenv("TMUX", raising=False)
    seen = _patch_run(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli.preview_step, "inline_image_escape", lambda p, width="100%": "[IMG]",
    )
    res = CliRunner().invoke(cli.app, ["preview", "--example", "lorem", "--inline-image"])
    assert res.exit_code == 0, res.output
    assert len(seen) == 1
    assert res.output.count("[IMG]") == 1


def test_preview_manual_example_passthrough(tmp_path, monkeypatch):
    """--example-primary/--example-secondary：手动构造示例文本，example 为空透传。"""
    from typer.testing import CliRunner
    from quicksrt import cli

    seen = _patch_run(monkeypatch, tmp_path)
    res = CliRunner().invoke(
        cli.app, ["preview", "--example-primary", "你好", "--example-secondary", "Hello"]
    )
    assert res.exit_code == 0, res.output
    assert seen[0]["example"] is None
    assert seen[0]["example_primary"] == "你好"
    assert seen[0]["example_secondary"] == "Hello"


def test_preview_manual_example_conflicts_with_example(tmp_path, monkeypatch):
    """--example 与 --example-primary/--example-secondary 互斥。"""
    from typer.testing import CliRunner
    from quicksrt import cli

    _patch_run(monkeypatch, tmp_path)
    res = CliRunner().invoke(
        cli.app, ["preview", "--example", "lorem", "--example-primary", "你好"]
    )
    assert res.exit_code != 0
    assert "互斥" in res.output


def test_preview_manual_example_blank_rejected(tmp_path, monkeypatch):
    """手动示例文本全空白：BadParameter，不渲染。"""
    from typer.testing import CliRunner
    from quicksrt import cli

    seen = _patch_run(monkeypatch, tmp_path)
    res = CliRunner().invoke(cli.app, ["preview", "--example-primary", " "])
    assert res.exit_code != 0
    assert "至少提供一个非空文本" in res.output
    assert not seen  # 未渲染
