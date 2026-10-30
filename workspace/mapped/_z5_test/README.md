# word_count

A small, dependency-free `wc`-style utility plus its test suite.

- `word_count.py` — the command line tool.
- `test_word_count.py` — the test suite (`unittest`, stdlib only).
- `demo1.txt`, `demo2.txt` — sample inputs used for the manual walkthrough.

## Usage

```
python word_count.py [-h] [-e NAME] [-b] [--version] FILE [FILE ...]
```

One line of output is printed per file, in the order the files were given:

```
$ python word_count.py demo1.txt demo2.txt
demo1.txt: 2 lines, 9 words, 46 chars
demo2.txt: 1 lines, 1 words, 7 chars
```

`-e/--encoding` picks the text encoding (default `utf-8`). `-b/--bytes`
reports the file size in bytes instead of the character count.

Counting rules: lines follow `str.splitlines()`, so a final line without a
trailing newline still counts. Words are runs of non-whitespace characters.
Characters are Unicode code points, not bytes.

If one file cannot be read, the error goes to stderr and the remaining
files are still counted.

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | every file was counted successfully |
| 1 | at least one file could not be read (missing, directory, permission, bad encoding) |
| 2 | invalid command line (unknown option, no file given, unknown `--encoding` name) |

## Running the tests

From this directory:

```
python -m unittest test_word_count -v
```

The suite has 31 tests covering the normal counting paths and the failure
paths (missing file, directory given as input, permission denied,
undecodable bytes, unknown encoding, missing arguments, unknown option).
