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


def test_preview_preset_multi(tmp_path, monkeypatch):
    """--preset a,b：按逗号分隔逐个渲染。"""
    from typer.testing import CliRunner
    from quicksrt import cli

    seen = _patch_run(monkeypatch, tmp_path)
    res = CliRunner().invoke(cli.app, ["preview", "--example", "lorem", "--preset", "plex,plex_yellow"])
    assert res.exit_code == 0, res.output
    assert [kw["preset"] for kw in seen] == ["plex", "plex_yellow"]
    assert res.output.count("preview 完成:") == 2


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


def test_preview_all_preset_inline(tmp_path, monkeypatch):
    """--all-preset --inline-image：每张图一个转义序列、各自独占一行（100% 宽度）。"""
    from typer.testing import CliRunner
    from quicksrt import cli
    from quicksrt.config import load_config

    _patch_run(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli.preview_step, "inline_image_escape",
        lambda p, width="100%": f"[IMG:{width}]",
    )
    n = len(load_config("config.toml").presets)
    res = CliRunner().invoke(cli.app, ["preview", "--example", "lorem", "--all-preset", "--inline-image"])
    assert res.exit_code == 0, res.output
    # 每张图 100% 宽度、转义序列后各有一个换行（一个一行）
    assert res.output.count("[IMG:100%]\n") == n
    # 预设名单独一行，图片在下一行（按名排序）
    for p in sorted(load_config("config.toml").presets):
        assert f"[{p}]\n[IMG:100%]\n" in res.output
