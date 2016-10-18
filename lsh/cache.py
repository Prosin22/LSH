# -*- coding: utf-8 -*-
from __future__ import division

import json
from collections import defaultdict
import itertools
import logging
from copy import deepcopy

import numpy as np
from lsh.minhash import MinHasher

__author__ = "Matti Lyra"


class Cache(object):
    """LSH provides a way of determining the local neighbourhood of a document.

    Locality Sensitive Hashing relies on probabilistic guarantees of a hash
    function family to produce hash collisions for similar content. The
    implementation uses MinHash to produce those collisions and allows for fast
    deduplication of data sets without having to do all pairs comparisons.
    """

    def __init__(self, hasher, num_bands=10, **kwargs):
        # each fingerprint is divided into n bins (bands) and duplicate
        # documents are computed only for documents that land in the same
        # bucket in one of the bins
        # bins[idx of band where docs may overlap][hash of fingerprint] ->
        # list of doc ids that have that fingerprint segment at that position
        self.bins = [defaultdict(set) for _ in range(num_bands)]
        self.hasher = hasher
        msg = 'The number of seeds in the fingerprint must ' \
              'be divisible by the number of bands'
        assert hasher.num_seeds % num_bands == 0, msg
        self.band_width = hasher.num_seeds // num_bands
        self.num_bands = num_bands

        self.fingerprints = dict()

    def bins_(self, fingerprint):
        yield from enumerate(np.array_split(fingerprint, self.num_bands))

    def clear(self):
        self.bins = [defaultdict(set) for _ in range(self.num_bands)]
        self.hasher.fingerprint.cache_clear()

    def to_json(self, path):
        with open(path, 'w') as outf:
            json.dump(self.jsonable(), outf)

    @staticmethod
    def from_json(path):
        with open(path) as inf:
            data = json.load(inf)

        cache = Cache(MinHasher.from_json_str(data.pop('hasher')),
                      **data)
        bins = []
        for bin in data['bins']:
            b1 = defaultdict(set)
            b1.update({k: set(v) for k, v in bin.items()})
            bins.append(b1)
        cache.bins = bins

        key_typecast = {
            'int': int,
            'str': str,
            '': lambda x: x
        }
        func = key_typecast[data.pop('id_key_type', '')]
        cache.fingerprints = {func(k[0]): np.array(v)
                              for k, v in data['fingerprints'].items()}
        return cache

    def jsonable(self):
        d = deepcopy(self.__dict__)
        d['hasher'] = d['hasher'].jsonable()
        d['fingerprints'] = {k: v.tolist()
                             for k, v in d['fingerprints'].items()}
        if d['fingerprints']:
            sample_id = list(d['fingerprints'].keys())[0]
            if isinstance(sample_id, str):
                d['id_key_type'] = 'str'
            if isinstance(sample_id, int):
                d['id_key_type'] = 'int'
        bins = []
        for b in self.bins:
            # b is a defaultdict(int->set[int])
            b1 = {k: list(v) for k, v in b.items()}
            bins.append(b1)
        d['bins'] = bins
        return d

    def update(self, doc, doc_id):
        fingerprint = self.hasher.fingerprint(doc.encode('utf8'))

        if doc_id is not None and doc_id in self.fingerprints:
            # todo is this a problem? should we refuse to add it?
            logging.warning('Duplicate id %d', doc_id)
        self.fingerprints[doc_id] = fingerprint

        for bin_i, bucket in self.bins_(fingerprint):
            # todo faster hash here? or no hash at all?
            bucket_id = hash(tuple(bucket))
            self.bins[bin_i][bucket_id].add(doc_id)

    def filter_candidates(self, candidate_id_pairs, min_jaccard):
        logging.info('Computing Jaccard sim of %d pairs',
                     len(candidate_id_pairs))
        res = set()
        for id1, id2 in candidate_id_pairs:
            # todo id1, id2 may not be contained in data
            jaccard = self.hasher.jaccard(self.fingerprints[id1],
                                          self.fingerprints[id2])
            if jaccard > min_jaccard:
                res.add((id1, id2))
        logging.info('Keeping %d/%d candidate duplicate pairs',
                     len(res), len(candidate_id_pairs))
        return res

    def get_all_duplicates(self, min_jaccard=None):
        candidate_pairs = set()
        for b in self.bins:
            for bucket_id in b:
                if len(b[bucket_id]) > 1:
                    pairs_ = set(itertools.combinations(b[bucket_id], r=2))
                    candidate_pairs.update(pairs_)
        if min_jaccard is None:
            return candidate_pairs

        return self.filter_candidates(candidate_pairs, min_jaccard)

    def get_duplicates_of(self, doc=None, doc_id=None, min_jaccard=None):
        if doc_id is not None and doc_id in self.fingerprints:
            fingerprint = self.fingerprints[doc_id]
        elif doc is not None:
            fingerprint = self.hasher.fingerprint(doc.encode('utf8'))
        else:
            raise ValueError('Must provide a document or a know document id')

        candidates = set()
        for bin_i, bucket in self.bins_(fingerprint):
            bucket_id = hash(tuple(bucket))
            candidates.update(self.bins[bin_i][bucket_id])

        if min_jaccard is None:
            return candidates
        else:
            return {x for x in candidates
                    if self.hasher.jaccard(fingerprint,
                                           self.fingerprints[x]) > min_jaccard}

    def is_duplicate(self, doc, doc_id=None):
        if doc_id is not None and doc_id in self.fingerprints:
            return False

        return len(self.get_duplicates_of(doc) - {doc_id}) > 0
