# oliviercoulon.github.io

Personal research page of Olivier Coulon (CNRS, Institut de Neurosciences de la Timone, Marseille),
published with GitHub Pages at <https://oliviercoulon.github.io/>.

**To update the site (add a publication, change a text or an image), see [UPDATING.md](UPDATING.md).**

## How it works

`build.py` generates the static site in `_site/` from:

| Path | Content |
|---|---|
| `content/publications.bib` | publication list (BibTeX), sorted by year on the page; DOI-only entries are completed from Crossref |
| `content/site.yaml` | identity, menu, contact details, profile links, logos |
| `content/home.yaml` | home page texts, research themes, projects, software |
| `content/contact.yaml` | contact page and the "About this site" notice |
| `templates/` | Jinja2 page templates |
| `static/` | CSS, JavaScript, images |

Build and preview locally:

```bash
pip install -r requirements.txt
```

```bash
python3 build.py --serve
```

The site is served on <http://127.0.0.1:8150/>. `_site/` is not committed.

## Deployment

`.github/workflows/pages.yml` builds and publishes the site at every push to `main`.
One-time setting in the repository: *Settings → Pages → Build and deployment → Source: GitHub Actions*.
The repository must be named `oliviercoulon.github.io` for the site to be served at the root URL;
project sites such as <https://oliviercoulon.github.io/babofet/> keep working alongside it.
