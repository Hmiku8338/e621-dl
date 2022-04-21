from __future__ import annotations

import os
import os.path
import re
import shutil
import sys
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import requests
import typer
from e621.api import E621
from e621.enums import PoolCategory
from e621.models import Pool, Post
from tqdm import tqdm

CURRENT_DIR = Path(__file__).parent
USERNAME_FILE = CURRENT_DIR / "e621-dl_username.txt"
API_KEY_FILE = CURRENT_DIR / "e621-dl_api_key.txt"
VALID_FILE_NAME = re.compile(r"\d+ (?P<post_id>\d+)")
api = E621(
    (USERNAME_FILE.read_text(), API_KEY_FILE.read_text()) if USERNAME_FILE.exists() and API_KEY_FILE.exists() else None,
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


class PoolOrder(str, Enum):
    NAME = "name"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    POST_COUNT = "post_count"


@posts_app.command("search")
def search_posts(
    tags: List[str] = typer.Argument(..., help="Tags to search for"),
    max_posts: int = typer.Option(
        sys.maxsize,
        "-m",
        "--max_posts",
        help="The program will stop after downloading n posts",
        metavar="n",
    ),
    download_dir: Path = dir_arg,
    save_space: bool = save_space_arg,
    _hardcoded_download_dir: Optional[Path] = typer.Option(None, hidden=True),
) -> List[Post]:
    """Download posts that match the given set of tags"""
    formatted_tags = " ".join(normalize_tags(tags))
    print("Searching for posts...")
    posts = api.posts.search(formatted_tags, limit=max_posts, ignore_pagination=True)
    print(len(posts), "posts found")
    if _hardcoded_download_dir is None:
        directory = download_dir / formatted_tags
        directory.mkdir(exist_ok=True, parents=True)
    else:
        directory = _hardcoded_download_dir
    if save_space:
        post_managers: Dict[int, PostManager] = {}
        find_all_posts(directory.parent, post_managers)

        optimized_posts = 0
        for index, post in enumerate(posts, start=1):
            if post.id in post_managers and post_managers[post.id].copies and post.file is not None:
                optimized_posts += 1
                dst = directory / get_post_name(index, post.id, post.file.ext)
                if not dst.is_file():
                    shutil.copyfile(post_managers[post.id].copies[0], dst)
        print(optimized_posts, "posts already downloaded")
    mass_enumerated_download(posts, directory, api)

    if save_space:
        clean([directory.parent], True)
    return posts


@posts_app.command("get")
def get_posts(post_ids: List[int], download_dir: Path = dir_arg, save_space: bool = save_space_arg) -> List[Post]:
    """Download a posts with the given ids"""
    return search_posts(
        [f'id:{",".join(map(str, post_ids))}'],
        max_posts=sys.maxsize,
        _hardcoded_download_dir=download_dir,
        save_space=save_space,
    )


@pools_app.command("search")
def search_pools(
    name_matches: Optional[str] = typer.Option(None, "-n", "--name-matches"),
    id: Optional[List[int]] = typer.Option(None, "-i", "--id"),
    description_matches: Optional[str] = typer.Option(None, "-D", "--description-matches"),
    creator_name: Optional[str] = typer.Option(None, "-N", "--creator-name"),
    creator_id: Optional[int] = typer.Option(None, "-C", "--creator-id"),
    is_active: Optional[bool] = typer.Option(None, "--is-active/--is-not-active"),
    is_deleted: Optional[bool] = typer.Option(None, "--is-deleted/--is-not-deleted"),
    category: Optional[PoolCategory] = typer.Option(None, "-c", "--category"),
    order: Optional[PoolOrder] = typer.Option(None, "-o", "--order"),
    max_pools: int = typer.Option(
        sys.maxsize,
        "-m",
        "--max_pools",
        help="The program will stop after downloading n pools",
        metavar="n",
    ),
    download_dir: Path = dir_arg,
    save_space: bool = save_space_arg,
) -> List[Pool]:
    """Download pools that match the given query"""
    pools = api.pools.search(
        name_matches=name_matches,
        id=id,
        description_matches=description_matches,
        creator_name=creator_name,
        creator_id=creator_id,
        is_active=is_active,
        is_deleted=is_deleted,
        category=category,
        order=order,  # type: ignore
        limit=max_pools,
        ignore_pagination=True,
    )
    for pool in pools:
        directory = download_dir / pool.name
        directory.mkdir(parents=True, exist_ok=True)
        print(len(pool.posts), "posts found in pool", pool.name)
        mass_enumerated_download(list(reversed(pool.posts)), directory, api)
    if save_space:
        clean([download_dir], True)
    return pools


@pools_app.command("get")
def get_pools(pool_ids: List[int], download_dir: Path = dir_arg, save_space: bool = save_space_arg) -> List[Pool]:
    """Download all posts in a pool with a given id"""
    return search_pools(
        id=pool_ids,
        max_pools=sys.maxsize,
        download_dir=download_dir,
        save_space=save_space,
        name_matches=None,
        description_matches=None,
        creator_name=None,
        creator_id=None,
        is_active=None,
        is_deleted=None,
        category=None,
        order=None,
    )


@app.command()
def clean(
    dirs: List[Path] = typer.Argument(..., help="Path to directories with objects"),
    download_broken_symlinks: bool = typer.Option(True, "-d", "--download-broken-symlinks"),
) -> None:
    """Replace all post duplicates in the given set of directories with symlinks"""
    if not dirs:
        dirs = [Path.cwd()]
    post_managers: Dict[int, PostManager] = {}
    for d in dirs:
        find_all_posts(d, post_managers)
    ids_to_download: List[tuple[str, Path]] = []
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
    posts_to_download = [
        (post.file.url, path, post.file.size) for ((_, path), post) in zipped_posts if post.file is not None
    ]
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


def mass_enumerated_download(posts: Sequence[Post], directory: Path, api: E621) -> None:
    """Download multuple files to the given directory."""
    enumerated_posts = [
        (p.file.url, directory / get_post_name(i, p.id, p.file.ext), p.file.size)
        for i, p in enumerate(posts, start=1)
        if p.file is not None
    ]
    diff_between_sizes = abs(len(posts) - len(enumerated_posts))
    if diff_between_sizes != 0:
        print(f"Skipping {diff_between_sizes} posts with no file available")
    return mass_download(enumerated_posts, api)


def mass_download(
    files: Iterable[tuple[str, Path, int]],
    api: E621,
    overwrite: bool = False,
) -> None:
    with requests.Session() as session:
        session.headers.update({"User-Agent": "e6tools"})
        session.auth = api.session.auth
        total_size = sum(size for _, _, size in files)
        progress_bar = tqdm(total=total_size, unit="iB", unit_scale=True)
        for url, path, size in files:
            progress_bar.set_description(f"Downloading {path.name}")
            if not path.exists() or overwrite:
                download_file(url, path, timeout=30)
            progress_bar.update(size)


def download_file(url: str, path: Path, session: Optional[requests.Session] = None, **kwargs) -> requests.Response:
    if session is None:
        r = requests.get(url, **kwargs)
    else:
        r = session.get(url, **kwargs)
    path.write_bytes(r.content)
    return r


def get_post_name(i: int, post_id: int, post_extension: str) -> str:
    return f"{i} {post_id}.{post_extension}"


class PostManager:
    def __init__(self, id: int):
        self.id = id
        self.copies: List[Path] = []
        self.links: List[Path] = []

    def replace_copies_with_symlinks(self):
        # We use shortest path as the original because it will be more likely to contain the original artist tag
        original = find_shortest_path(self.copies)
        self.copies.remove(original)
        for copy in self.copies + self.links:
            copy.unlink()
            copy.symlink_to(os.path.relpath(original, copy.parent))


def find_all_posts(d: Path, posts: Dict[int, PostManager]):
    for file_or_dir in d.iterdir():
        if file_or_dir.is_dir():
            find_all_posts(file_or_dir, posts)
        else:
            file = file_or_dir
            m = VALID_FILE_NAME.match(file.name)
            if m is None:
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


def find_shortest_path(paths: List[Path]) -> Path:
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


def normalize_tags(tags: List[str]) -> List[str]:
    tags = [t.lower().strip() for t in tags]
    tags.sort(reverse=True, key=sort_tag)
    return tags


if __name__ == "__main__":
    app()
