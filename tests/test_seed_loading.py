"""Seed priors must actually order the starting corpus.

`compute_seed_priority` combines a drug-likeness prior with a scaffold-diversity
bonus, but the bonus can only discriminate if the caller feeds it the corpus as
it fills. Called with no counts it returns a constant for every seed, which
silently reduces the starting order to file order.
"""

from biofuzz.corpus import Corpus, CorpusEntry
from biofuzz.seeds import compute_seed_priority, load_seeds


def _seed_file(tmp_path, pairs):
    path = tmp_path / "seeds.smi"
    path.write_text("".join(f"{smiles} {name}\n" for smiles, name in pairs))
    return path


def test_diversity_bonus_needs_the_corpus_census_to_discriminate():
    aspirin = "CC(=O)Oc1ccccc1C(=O)O"
    from rdkit import Chem
    from rdkit.Chem.Scaffolds import MurckoScaffold

    scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=Chem.MolFromSmiles(aspirin))

    without_counts = compute_seed_priority(aspirin)
    crowded = compute_seed_priority(aspirin, corpus_scaffold_counts={scaffold: 4})
    assert crowded < without_counts


def test_corpus_exposes_a_live_scaffold_census():
    """The census must be live, not a snapshot, so a loading loop sees growth."""
    corpus = Corpus()
    counts = corpus.scaffold_counts()
    assert counts == {}

    corpus.add(CorpusEntry(smiles="c1ccccc1O", base_priority=1.0))
    assert sum(counts.values()) == 1, "census did not reflect the insert"

    corpus.add(CorpusEntry(smiles="c1ccccc1N", base_priority=1.0))
    assert sum(counts.values()) == 2


def test_repeated_scaffold_seeds_get_decreasing_priors(tmp_path):
    """Two seeds sharing a scaffold must not both receive the first-of-series bonus."""
    # Three phenol-scaffold molecules plus one unrelated aliphatic.
    path = _seed_file(
        tmp_path,
        [
            ("c1ccccc1O", "phenol"),
            ("Cc1ccccc1O", "cresol"),
            ("CCc1ccccc1O", "ethylphenol"),
        ],
    )
    corpus = Corpus()
    priors = []
    for smiles, seed_id in load_seeds(path):
        prior = compute_seed_priority(
            smiles, corpus_scaffold_counts=corpus.scaffold_counts()
        )
        priors.append(prior)
        corpus.add(CorpusEntry(smiles=smiles, source_id=seed_id, base_priority=prior))

    assert corpus.size() == 3
    assert priors[0] > priors[-1], f"priors did not decay across a shared scaffold: {priors}"


def test_seed_prior_survives_insertion_as_base_priority():
    """A prior written to `priority` would be overwritten by the crowding term.

    Corpus derives `priority` from `base_priority` on every push, so the prior
    has to live in `base_priority` to survive.
    """
    corpus = Corpus()
    entry = corpus.add(CorpusEntry(smiles="CCO", source_id="s", base_priority=7.5))
    assert entry.base_priority == 7.5
    # Only scaffold crowding may reduce the effective priority, and a lone
    # entry has no crowding.
    assert entry.priority == 7.5
