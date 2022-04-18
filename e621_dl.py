from __future__ import annotations

import os
import os.path
import re
import shutil
from pathlib import Path
from typing import Iterable, Optional
import requests

import typer

from e621.models import Post
import rich.progress


from e621.api import E621

CURRENT_DIR = Path(__file__).parent
USERNAME_FILE = CURRENT_DIR / "username.txt"
API_KEY_FILE = CURRENT_DIR / "api_key.txt"
VALID_FILE_NAME = re.compile(r"\d+ (?P<post_id>\d+)")
api = E621(
    (USERNAME_FILE.read_text(), API_KEY_FILE.read_text()) if USERNAME_FILE.exists() and API_KEY_FILE.exists() else None,
)
progress_bar = rich.progress.Progress(
    rich.progress.TextColumn("[bold blue]{task.fields[filename]}", justify="right"),
    rich.progress.BarColumn(bar_width=None),
    "[progress.percentage]{task.percentage:>3.1f}%",
    transient=True,
)

app = typer.Typer(add_completion=False)
posts_app = typer.Typer(name="posts", help="Operations for post downloading")
pools_app = typer.Typer(name="pools", help="Operations for pool downloading")
app.add_typer(posts_app)
app.add_typer(pools_app)
dir_arg: Path = typer.Option(
    Path.cwd(),
    "--download_dir",
    "-d",
    file_okay=False,
    dir_okay=True,
    writable=True,
    readable=True,
    resolve_path=True,
    help="Path to download directory",
)
save_space_arg = typer.Option(False, "-s", "--save-space", help="Save space by turning duplicates into symlinks")


@posts_app.command("search")
def get_posts(
    tags: list[str] = typer.Argument(..., help="Tags to search for"),
    max_posts: int = typer.Option(
        10000,
        "-m",
        "--max_posts",
        help="The program will stop after downloading n posts",
        metavar="n",
    ),
    download_path: Path = dir_arg,
    save_space: bool = save_space_arg,
) -> None:
    """Download posts that match the given set of tags"""
    formatted_tags = " ".join(normalize_tags(tags))
    posts = api.posts.search(formatted_tags, limit=max_posts)
    print(len(posts), "posts found")
    directory = download_path / formatted_tags
    directory.mkdir(exist_ok=True, parents=True)
    if save_space:
        post_managers: dict[int, PostManager] = {}
        find_all_posts(download_path, post_managers)

        optimized_posts = 0
        for index, post in enumerate(posts, start=1):
            if post.id in post_managers and post_managers[post.id].copies:
                optimized_posts += 1
                dst = directory / get_post_name(post, index)
                if not dst.is_file():
                    shutil.copyfile(post_managers[post.id].copies[0], dst)
        print(optimized_posts, "posts already downloaded")
    mass_enumerated_download(posts, directory, api)

    if save_space:
        clean([download_path.parent], True)


@posts_app.command("get")
def get_post(post_id: int, download_dir: Path = dir_arg) -> Post:
    """Download a single post with a given id"""
    post = api.posts.get(post_id)
    download_path = download_dir / f"{post_id}.{post.file.ext}"
    with requests.Session() as session:
        session.auth = api.session.auth
        session.headers.update(api.session.headers)
        download_file(post.file.url, download_path, session)
    return post


@pools_app.command("get")
def get_pool(pool_id: int, download_path: Path = dir_arg, save_space: bool = save_space_arg) -> None:
    """Download all posts in a pool with a given id"""
    pool = api.pools.get(pool_id)
    directory = download_path / str(pool.name)
    directory.mkdir(parents=True, exist_ok=True)
    print(len(pool.posts), "posts found in pool", pool.name)
    mass_enumerated_download(reversed(pool.posts), directory, api)
    if save_space:
        clean([download_path.parent], True)


@app.command()
def clean(
    dirs: list[Path] = typer.Argument(..., help="Path to directories with objects"),
    download_broken_symlinks: bool = typer.Option(True, "-d", "--download-broken-symlinks"),
) -> None:
    """Replace all post duplicates in the given set of directories with symlinks"""
    if not dirs:
        dirs = [Path.cwd()]
    post_managers: dict[int, PostManager] = {}
    for d in dirs:
        find_all_posts(d, post_managers)
    ids_to_download: list[tuple[str, Path]] = []
    for post in post_managers.values():
        if not post.copies:
            if download_broken_symlinks:
                new_original_path = find_shortest_path(post.links)
                post.links.remove(new_original_path)
                post.copies.append(new_original_path)
                # Pathlib has a weird error where we can't overwrite a broken symlink
                new_original_path.unlink()
                ids_to_download.append((str(post.id), new_original_path))
        post.replace_copies_with_symlinks()
    posts = api.posts.get([int(id) for id, _ in ids_to_download])
    zipped_posts = zip(sorted(ids_to_download, key=lambda p: p[0]), sorted(posts, key=lambda p: p.id))
    posts_to_download = [(post.file.url, path, post.file.size) for ((_, path), post) in zipped_posts]
    mass_download(posts_to_download, api, overwrite=True)


@app.command()
def login(
    username: str = typer.Option(..., prompt=True),
    api_token: str = typer.Option(..., prompt=True),
) -> None:
    """Save username and api token information for automatic future use"""
    USERNAME_FILE.write_text(username), API_KEY_FILE.write_text(api_token)


@app.command()
def logout() -> None:
    """Remove pre-existing username and api token information"""
    API_KEY_FILE.unlink(), USERNAME_FILE.unlink()


BYTES_IN_MB = 10**6


def mass_enumerated_download(posts: Iterable[Post], directory: Path, api: E621) -> None:
    """Download multuple files to the given directory."""
    return mass_download(
        [(p.file.url, directory / get_post_name(p, i), p.file.size) for i, p in enumerate(posts, start=1)],
        api,
    )


def mass_download(
    files: Iterable[tuple[str, Path, int]],
    api: E621,
    overwrite: bool = False,
) -> None:
    with progress_bar, requests.Session() as session:
        session.headers.update({"User-Agent": "e6tools"})
        session.auth = api.session.auth
        total_size = sum(size for _, _, size in files) / BYTES_IN_MB
        task_id = progress_bar.add_task("[bold blue]Downloading...", total=total_size, visible=True, filename="")
        for url, path, size in files:
            progress_bar.update(task_id, filename=path.name)
            if not path.exists() or overwrite:
                download_file(url, path, timeout=30)
            progress_bar.update(task_id, advance=size / BYTES_IN_MB)


def download_file(url: str, path: Path, session: Optional[requests.Session] = None, **kwargs) -> requests.Response:
    if session is None:
        r = requests.get(url, **kwargs)
    else:
        r = session.get(url, **kwargs)
    path.write_bytes(r.content)
    return r


def get_post_name(post: Post, i: int) -> str:
    return f"{i} {post.id}.{post.file.ext}"


class PostManager:
    id: int
    copies: list[Path]
    links: list[Path]

    def __init__(self, id: int):
        self.id = id
        self.copies = []
        self.links = []

    def replace_copies_with_symlinks(self):
        # We use shortest path as the original because it will be more likely to contain the original artist tag
        original = find_shortest_path(self.copies)
        self.copies.remove(original)
        for copy in self.copies + self.links:
            copy.unlink()
            copy.symlink_to(os.path.relpath(original, copy.parent))


def find_all_posts(d: Path, posts: dict[int, PostManager]):
    for file_or_dir in d.iterdir():
        if file_or_dir.is_dir():
            find_all_posts(file_or_dir, posts)
        else:
            file = file_or_dir
            if (m := VALID_FILE_NAME.match(file.name)) is None:
                continue
            post_id = int(m["post_id"])
            if post_id not in posts:
                post = PostManager(post_id)
                posts[post_id] = post
            else:
                post = posts[post_id]
            if file.is_symlink():
                post.links.append(file)
            else:
                post.copies.append(file)


def find_shortest_path(paths: list[Path]) -> Path:
    return min(paths, key=lambda p: len(str(p.absolute())))


def sort_tag(tag: str) -> int:
    code = int.from_bytes(tag.encode(), "little", signed=False)
    if ":" in tag or tag.startswith("-"):
        # Meaning that we want it to be at the end.
        # The method used is so complex because we want each integer to be unique
        # so that the original order of elements does not matter.
        return -code
    else:
        return code


def normalize_tags(tags: list[str]) -> list[str]:
    tags = [t.lower().strip() for t in tags]
    tags.sort(reverse=True, key=sort_tag)
    return tags


if __name__ == "__main__":
    app()
