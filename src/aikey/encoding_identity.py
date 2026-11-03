"""Declared encoder contract fields, separate from checkpoint file revisions.

Changing an algorithm requires a new contract identifier and index migration.
The CLIP checkpoint digest already includes tokenizer.json bytes; these fields
also bind the fixed preprocessing/tokenization rules, modality and metric.
They describe the supported protocol, not independent server attestation.
"""

CLIP_ENCODING = {
    'modality': 'image-text',
    'distance_metric': 'cosine',
    'preprocessing': 'clip-rgb-shortest224-bicubic-center224-meanstd-v1',
    'tokenizer': 'checkpoint-tokenizer-json-lowercase-context77-v1',
}
TEXT_ENCODING = {
    'modality': 'text',
    'distance_metric': 'cosine',
    'preprocessing': 'e5-prefix512-masked-mean-l2-v1',
    'tokenizer': 'checkpoint-tokenizer-json-context512-v1',
}
UPGRADABLE_ENCODING = tuple(CLIP_ENCODING)


def artifact_revision(root, names):
    """Hash exact declared regular checkpoint/tokenizer files without following links."""
    import hashlib
    import os
    from pathlib import Path
    import stat
    digest = hashlib.sha256()
    total = 0
    for name in names:
        descriptor = os.open(Path(root)/name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 4*1024**3:
                raise ValueError('Encoder artifact is not a bounded regular file')
            total += before.st_size
            if total > 8*1024**3:
                raise ValueError('Encoder artifact set exceeds its byte bound')
            digest.update(name.encode()+b'\0')
            for block in iter(lambda: stream.read(1024*1024), b''):
                digest.update(block)
            after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('Encoder artifacts changed while hashing')
    return digest.hexdigest()
