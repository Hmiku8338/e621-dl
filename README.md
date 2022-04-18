# e621-dl
**e621-dl** is a a simple and fast e621 post/pool downloader. It is based upon the [e621](https://github.com/PatriotRossii/e621-py) api wrapper both in implementation and interface.

## Installation
`pip install e621-dl`

## Quickstart
* To download a post with a given id:  
`e6 posts get 12345`  
* To download all posts that match the canine but not 3d tags:  
`e6 posts search "canine 3d"`  
* To download 500 posts that match the 3d tag:  
`e6 posts search 3d -m 500`  
* To download posts that match the 3d tag to directory e621_downloads:  
`e6 posts search 3d -d e621_downloads`
* To download all posts that match the 3d tag and replace all post duplicates from the parent directory with symlinks:  
`e6 posts search 3d -s`  
* To download the pool with the given id:  
`e6 pools get 12345`
* To replace all post duplicates from the current directory with symlinks:  
`e6 clean`
* To save e621 login information to be used for every future query:  
`e6 login`
* To remove e621 login information:  
`e6 logout`

For advanced reference, use `--help` option. For example, `e6 --help`, `e6 posts search --help`, etc.