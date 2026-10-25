# Library tools

## Duplicate books

A command line tool finds the duplicate EPUBs of the library (`LIBRARY_DIR`), and can remove them:

```bash
python -m controllers.tools.duplicates                         # report only
python -m controllers.tools.duplicates --json                  # report as JSON
python -m controllers.tools.duplicates --move-to ../doublons   # move the extra copies out of the library
python -m controllers.tools.duplicates --delete                # delete the extra copies
# In the Docker image
docker exec cops python -m controllers.tools.duplicates --move-to /config/doublons
```

- **Identical files** (same SHA-256), wherever they are stored.
- **Same book**: different files whose metadata give the same title, author (word order, case, accents and punctuation ignored: "Asimov, Isaac" = "Isaac Asimov") and series position, with texts of similar lengths (`--identical-only` skips them).
- **To check**: same metadata but texts of very different lengths (one volume of a book published in several, abridged edition…): reported, never removed.

In each group one file is kept: the one already read on a reader (known to the sync database, so its progress stays attached to a library book), then the one with a cover, a readable file name containing the title (not garbled by a wrong encoding), metadata, the largest file, the oldest. Folders left empty are removed. `--move-to` must be outside the library, otherwise the copies would still appear in the catalog.

## Audiobook voices

A command line tool creates a voice of the audiobook from a written description, without any recording of a real person: the voice design model of the settings (`design_model` of [`audiobook.toml`](../audiobook.toml), Qwen3-TTS « VoiceDesign ») reads a reference text with a voice made from the description, and the recording becomes a cloned voice, read by the cloning model (`clone_model`) for the whole book (see [`AUDIOBOOK_CONFIG_PATH`](installation.md#configuration)).

```bash
python -m controllers.tools.voice_design narrateur "Voix masculine française d'une cinquantaine d'années, grave, chaude et veloutée, légèrement feutrée. Narrateur professionnel de livre audio : diction parfaite, accent français standard, débit posé, intonation naturelle et expressive sans excès."
python -m controllers.tools.voice_design narratrice "Voix féminine française d'une quarantaine d'années, chaude, douce et légèrement grave, timbre velouté et rassurant. Narratrice professionnelle de livre audio : diction parfaite, accent français standard, débit posé, intonation naturelle et expressive sans excès."
# In the Docker image: written to /config/voix/<name>.wav, next to AUDIOBOOK_CONFIG_PATH
docker exec cops python -m controllers.tools.voice_design narrateur "…"
```

The recording (`voix/<name>.wav` next to the settings file, or `--output`) is about fifteen seconds of a fixed French text (`--text` to read another one); the tool prints the `[clones.<name>]` table to add to the settings, then restart the server. The design model invents the voice again on each call: listen to the recording (`aplay voix/narrateur.wav`) and run the tool again until one pleases. Describe the gender, age, timbre, accent and reading style; the descriptions above gave the two voices of the example settings.

## Fixing metadata (genres, authors, titles)

A command line tool lists the books whose metadata need fixing in a CSV file, then writes the completed file back into the EPUBs:

```bash
python -m controllers.tools.metadata_fix export livres.csv            # books to fix, with genre suggestions
python -m controllers.tools.metadata_fix export livres.csv --online   # suggestions from Open Library too
python -m controllers.tools.metadata_fix apply livres.csv             # check the completed file (dry run)
python -m controllers.tools.metadata_fix apply livres.csv --write     # write into the EPUBs
# In the Docker image (the library volume must be writable)
docker exec cops python -m controllers.tools.metadata_fix export /config/livres.csv
docker exec cops python -m controllers.tools.metadata_fix apply /config/livres.csv --write && docker restart cops
```

`export` lists the books with no genre shown in the catalog (none, or only subjects that are not genres of `genres.toml`), a missing author ("Unknown", "Inconnu"…) or a title to clean ("untitled", "(French Edition)", ".epub"…). The `suggestion` column holds the genres of the author's other books (the genres of at least 40 % of them, for authors with 3 books or more); with `--online`, Open Library is searched for the others (only the title and author are sent).

The CSV is UTF-8, comma separated, with a header line; rows may be reordered or left empty, columns must stay:

| Column | Content |
|---|---|
| `chemin` | Path relative to the library: the key of the row, **do not change it** |
| `probleme` | What is wrong (`aucun genre`, `genre non affiché…`, `auteur manquant`, `titre à nettoyer`), read only |
| `titre`, `auteur`, `serie`, `tome`, `annee`, `description` | Current metadata (description cut at 500 characters), read only |
| `suggestion` | Suggested genres, read only (used by `apply --accept-suggestions` when `genres` is empty) |
| `genres` | **To fill**: 1 to 3 genres of `genres.toml` (or their aliases: "SF", "Polar"…), separated by `;` |
| `titre_corrige` | Optional: the title alone, without volume number, year or publisher mention |
| `auteur_corrige` | Optional: "First Last", several authors separated by ` & ` |

An empty cell changes nothing. `apply` reports the unknown genres and missing files, then (with `--write`) adds the genres as `dc:subject` and replaces `dc:title` / `dc:creator` in the OPF only: the other entries are kept, the result is read back before replacing the file, and a calibre series named after a genre ("Thriller", which would hide the subject of the same name) is removed. The file modification time is kept, so the books do not come back in "Recent Books": **restart the server** to read the new metadata. A rewritten EPUB gets a new KOReader document hash; the server still finds the books synced by their file name. Back up the library first.
