"""Command line interface.

``forage run`` executes the whole pipeline in dependency order. Individual
stages can be re-run on their own - each reads what it needs from
``data/interim/<aoi>/``, so retuning weights and rebuilding the map does not
mean re-downloading imagery.
"""

from __future__ import annotations

import time

import typer
from rich.console import Console

from .config import load_config
from .stages import access, export, forest, landstatus, observations, scoring, terrain, vegetation

app = typer.Typer(add_completion=False, help="Foraging suitability mapper.")
console = Console()

STAGES = {
    "terrain": terrain.run,
    "vegetation": vegetation.run,
    "forest": forest.run,
    "access": access.run,
    "observations": observations.run,
    "landstatus": landstatus.run,
    "scoring": scoring.run,
    "export": export.run,
}

ORDER = ["terrain", "vegetation", "forest", "access", "observations", "landstatus", "scoring", "export"]

ConfigOpt = typer.Option("config/pipeline.json", "--config", "-c", help="Pipeline config file.")
AoiOpt = typer.Option(None, "--aoi", help="AOI polygon, overriding the config. Re-targets the whole pipeline.")
SpeciesOpt = typer.Option(None, "--species", help="Species profile, overriding the config.")


def _log(msg: str = "") -> None:
    """Stage output. Markup is disabled because stages prefix their lines with
    tags like ``[access]``, which Rich would otherwise parse as a style and
    swallow."""
    console.print(msg, highlight=False, markup=False)


def _say(msg: str = "") -> None:
    """CLI's own output, where Rich markup is intentional."""
    console.print(msg, highlight=False)


def _run_stage(name: str, cfg, **kwargs):
    started = time.time()
    result = STAGES[name](cfg, log=_log, **kwargs)
    _say(f"[dim]  {name} finished in {time.time() - started:.1f}s[/dim]\n")
    return result


@app.command()
def run(
    config: str = ConfigOpt,
    skip: str = typer.Option("", help="Comma-separated stages to skip."),
    only: str = typer.Option("", help="Comma-separated stages to run, in pipeline order."),
    aoi: str = AoiOpt,
    species: str = SpeciesOpt,
    reuse_imagery: bool = typer.Option(
        False, "--reuse-imagery",
        help="Reuse cached Sentinel-2 index composites instead of re-downloading."),
):
    """Run the full pipeline: terrain -> vegetation/forest -> access -> observations -> land status -> scoring -> export."""
    cfg = load_config(config, aoi=aoi, species=species)
    _banner(cfg)

    skipped = {s.strip() for s in skip.split(",") if s.strip()}
    wanted = [s.strip() for s in only.split(",") if s.strip()] or ORDER
    unknown = (skipped | set(wanted)) - set(ORDER)
    if unknown:
        raise typer.BadParameter(f"unknown stage(s): {', '.join(sorted(unknown))}")

    started = time.time()
    for name in ORDER:
        if name not in wanted or name in skipped:
            continue
        kwargs = {"reuse_indices": reuse_imagery} if name == "vegetation" else {}
        _run_stage(name, cfg, **kwargs)

    _say(f"[bold green]Pipeline complete in {time.time() - started:.1f}s[/bold green]")
    _say(f"Ranked sites: {cfg.output('sites.geojson').relative_to(cfg.root)}")
    _say(f"Web assets:   web/data/{cfg.run_id}/")
    _say("\nServe the map with:  [bold]python -m http.server -d web 8000[/bold]")


def _stage_command(name: str):
    def command(config: str = ConfigOpt, aoi: str = AoiOpt, species: str = SpeciesOpt):
        cfg = load_config(config, aoi=aoi, species=species)
        _banner(cfg)
        _run_stage(name, cfg)
    command.__name__ = name
    command.__doc__ = f"Run the {name} stage on its own."
    return command


for _name in ORDER:
    app.command(name=_name)(_stage_command(_name))


@app.command()
def areas(config: str = ConfigOpt):
    """List AOI polygons available in config/aoi/."""
    cfg = load_config(config)
    aoi_dir = cfg.root / "config" / "aoi"
    active = cfg.aoi_path.resolve()
    for path in sorted(aoi_dir.glob("*.geojson")):
        mark = "[bold green]*[/bold green]" if path.resolve() == active else " "
        _say(f" {mark} {path.stem}  [dim]({path.relative_to(cfg.root)})[/dim]")
    _say("\nPoint `aoi` in the config file at another polygon to re-target the pipeline.")


def _banner(cfg) -> None:
    sp = cfg.species
    _say(f"[bold]{cfg.aoi_label}[/bold]  |  {sp.get('common_name')} "
         f"([italic]{sp.get('scientific_name')}[/italic])")
    _say(f"[dim]grid {cfg.resolution:g} m  |  origin {cfg.pipeline['access']['origin']['name']}[/dim]\n")


if __name__ == "__main__":
    app()
