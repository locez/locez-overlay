# locez-overlay

> I come back to Gentoo!!

## Usage

Via `eselect repository`:

```bash
eselect repository add locez git https://github.com/locez/locez-overlay.git
emaint sync -r locez
```

Or `/etc/portage/repos.conf/locez.conf`:

```ini
[locez]
location = /var/db/repos/locez
sync-type = git
sync-uri = https://github.com/locez/locez-overlay.git
auto-sync = yes
```

```bash
emaint sync -r locez
```
