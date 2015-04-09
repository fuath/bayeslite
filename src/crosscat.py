# -*- coding: utf-8 -*-

#   Copyright (c) 2010-2014, MIT Probabilistic Computing Project
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

import json
import math
import time

import bayeslite.core as core
import bayeslite.guess as guess
import bayeslite.metamodel as metamodel

from bayeslite.sqlite3_util import sqlite3_quote_name
from bayeslite.util import arithmetic_mean
from bayeslite.util import casefold
from bayeslite.util import unique

crosscat_schema_v1 = '''
INSERT INTO bayesdb_metamodel (name, version) VALUES ('crosscat', 1);

CREATE TABLE bayesdb_crosscat_disttype (
	name		TEXT NOT NULL PRIMARY KEY,
	stattype	TEXT NOT NULL REFERENCES bayesdb_stattype(name),
	default_dist	BOOLEAN NOT NULL,
	UNIQUE(stattype, default_dist)
);

INSERT INTO bayesdb_crosscat_disttype (name, stattype, default_dist)
    VALUES
        ('normal_inverse_gamma', 'numerical', 1),
        ('symmetric_dirichlet_discrete', 'categorical', 1),
        ('vonmises', 'cyclic', 1);

CREATE TABLE bayesdb_crosscat_metadata (
	generator_id	INTEGER NOT NULL PRIMARY KEY
				REFERENCES bayesdb_generator(id),
	metadata_json	BLOB NOT NULL
);

CREATE TABLE bayesdb_crosscat_column (
	generator_id	INTEGER NOT NULL REFERENCES bayesdb_generator(id),
	colno		INTEGER NOT NULL CHECK (0 <= colno),
	cc_colno	INTEGER NOT NULL CHECK (0 <= cc_colno),
	disttype	TEXT NOT NULL,
	PRIMARY KEY(generator_id, colno),
	FOREIGN KEY(generator_id, colno)
		REFERENCES bayesdb_generator_column(generator_id, colno),
	UNIQUE(generator_id, cc_colno)
);

CREATE TABLE bayesdb_crosscat_column_codemap (
	generator_id	INTEGER NOT NULL REFERENCES bayesdb_generator(id),
	cc_colno	INTEGER NOT NULL CHECK (0 <= cc_colno),
	code		INTEGER NOT NULL,
	value		TEXT NOT NULL,
	FOREIGN KEY(generator_id, cc_colno)
		REFERENCES bayesdb_crosscat_column(generator_id, cc_colno),
	UNIQUE(generator_id, cc_colno, code),
	UNIQUE(generator_id, cc_colno, value)
);

CREATE TABLE bayesdb_crosscat_theta (
	generator_id	INTEGER NOT NULL REFERENCES bayesdb_generator(id),
	modelno		INTEGER NOT NULL,
	theta_json	BLOB NOT NULL,
	PRIMARY KEY(generator_id, modelno),
	FOREIGN KEY(generator_id, modelno)
		REFERENCES bayesdb_generator_model(generator_id, modelno)
);
'''

class CrosscatMetamodel(metamodel.IMetamodel):
    def __init__(self, crosscat):
        self._crosscat = crosscat

    def _crosscat_cache_nocreate(self, bdb):
        if bdb.cache is None:
            return None
        if 'crosscat' not in bdb.cache:
            return None
        return self._crosscat_cache(bdb)

    def _crosscat_cache(self, bdb):
        if bdb.cache is None:
            return None
        if 'crosscat' in bdb.cache:
            return bdb.cache['crosscat']
        else:
            cc_cache = CrosscatCache()
            bdb.cache['crosscat'] = cc_cache
            return cc_cache

    def _crosscat_metadata(self, bdb, generator_id):
        cc_cache = self._crosscat_cache(bdb)
        if cc_cache is not None and generator_id in cc_cache.metadata:
            return cc_cache.metadata[generator_id]
        sql = '''
            SELECT metadata_json FROM bayesdb_crosscat_metadata
                WHERE generator_id = ?
        '''
        cursor = bdb.sql_execute(sql, (generator_id,))
        try:
            row = cursor.next()
        except StopIteration:
            generator = core.bayesdb_generator_name(bdb, generator_id)
            raise ValueError('No crosscat metadata for generator: %s' %
                (generator,))
        else:
            metadata = json.loads(row[0])
            if cc_cache is not None:
                cc_cache.metadata[generator_id] = metadata
            return metadata

    def _crosscat_data(self, bdb, generator_id, M_c):
        table_name = core.bayesdb_generator_table(bdb, generator_id)
        qt = sqlite3_quote_name(table_name)
        columns_sql = '''
            SELECT c.name, c.colno
                FROM bayesdb_column AS c,
                    bayesdb_generator AS g,
                    bayesdb_generator_column AS gc
                WHERE g.id = ?
                    AND c.tabname = g.tabname
                    AND c.colno = gc.colno
                    AND gc.generator_id = g.id
                ORDER BY c.colno ASC
        '''
        columns = list(bdb.sql_execute(columns_sql, (generator_id,)))
        colnames = [name for name, _colno in columns]
        qcns = map(sqlite3_quote_name, colnames)
        cursor = bdb.sql_execute('SELECT %s FROM %s' % (','.join(qcns), qt))
        return [[crosscat_value_to_code(bdb, generator_id, M_c, colno, value)
                for value, (_name, colno) in zip(row, columns)]
            for row in cursor]

    def _crosscat_thetas(self, bdb, generator_id):
        sql = '''
            SELECT modelno FROM bayesdb_crosscat_theta
                WHERE generator_id = ?
        '''
        modelnos = (row[0] for row in bdb.sql_execute(sql, (generator_id,)))
        return dict((modelno, self._crosscat_theta(bdb, generator_id, modelno))
            for modelno in modelnos)

    def _crosscat_theta(self, bdb, generator_id, modelno):
        cc_cache = self._crosscat_cache(bdb)
        if cc_cache is not None and \
           generator_id in cc_cache.thetas and \
           modelno in cc_cache.thetas[generator_id]:
            return cc_cache.thetas[generator_id][modelno]
        sql = '''
            SELECT theta_json FROM bayesdb_crosscat_theta
                WHERE generator_id = ? AND modelno = ?
        '''
        cursor = bdb.sql_execute(sql, (generator_id, modelno))
        try:
            row = cursor.next()
        except StopIteration:
            generator = core.bayesdb_generator_name(bdb, generator_id)
            raise ValueError('No such crosscat model for generator %s: %d' %
                (repr(generator), modelno))
        else:
            theta = json.loads(row[0])
            if cc_cache is not None:
                if generator_id in cc_cache.thetas:
                    assert modelno not in cc_cache.thetas[generator_id]
                    cc_cache.thetas[generator_id][modelno] = theta
                else:
                    cc_cache.thetas[generator_id] = {modelno: theta}
            return theta

    def _crosscat_latent_stata(self, bdb, generator_id):
        thetas = self._crosscat_thetas(bdb, generator_id)
        return ((thetas[modelno]['X_L'], thetas[modelno]['X_D'])
            for modelno in sorted(thetas.iterkeys()))

    def _crosscat_latent_state(self, bdb, generator_id):
        return [statum[0] for statum
            in self._crosscat_latent_stata(bdb, generator_id)]

    def _crosscat_latent_data(self, bdb, generator_id):
        return [statum[1] for statum
            in self._crosscat_latent_stata(bdb, generator_id)]

    def name(self):
        return 'crosscat'

    def register(self, bdb):
        with bdb.savepoint():
            schema_sql = 'SELECT version FROM bayesdb_metamodel WHERE name = ?'
            cursor = bdb.sql_execute(schema_sql, (self.name(),))
            try:
                row = cursor.next()
            except StopIteration:
                # XXX WHATTAKLUDGE!
                for stmt in crosscat_schema_v1.split(';'):
                    bdb.sql_execute(stmt)
            else:
                version = row[0]
                if version != 1:
                    raise ValueError('Crosscat already installed'
                        ' with unknown schema version: %d' % (version,))

    def create_generator(self, bdb, table, schema, instantiate):
        do_guess = False
        columns = []
        for directive in schema:
            if isinstance(directive, list) and \
               len(directive) == 2 and \
               isinstance(directive[0], (str, unicode)) and \
               casefold(directive[0]) == 'guess' and \
               directive[1] == ['*']:
                do_guess = True
                continue
            if isinstance(directive, list) and \
               len(directive) == 2 and \
               isinstance(directive[0], (str, unicode)) and \
               casefold(directive[0]) != 'guess' and \
               isinstance(directive[1], (str, unicode)) and \
               casefold(directive[1]) != 'guess':
                columns.append((directive[0], directive[1]))
                continue
            raise ValueError('Invalid crosscat column model: %s' %
                (repr(directive),))

        with bdb.savepoint():
            # If necessary, guess the column statistical types.
            #
            # XXX Allow passing count/ratio cutoffs, and other
            # parameters.
            if do_guess:
                column_names = core.bayesdb_table_column_names(bdb, table)
                qt = sqlite3_quote_name(table)
                rows = list(bdb.sql_execute('SELECT * FROM %s' % (qt,)))
                stattypes = guess.bayesdb_guess_stattypes(column_names, rows,
                    overrides=columns)
                columns = zip(column_names, stattypes)
                columns = [(name, stattype) for name, stattype in columns
                    if stattype not in ('key', 'ignore')]

            # Create the metamodel-independent records and assign a
            # generator id.
            generator_id, column_list = instantiate(columns)

            # Install the metadata json blob.
            M_c = create_metadata(bdb, generator_id, column_list)
            insert_metadata_sql = '''
                INSERT INTO bayesdb_crosscat_metadata
                    (generator_id, metadata_json)
                    VALUES (?, ?)
            '''
            metadata_json = json.dumps(M_c)
            bdb.sql_execute(insert_metadata_sql, (generator_id, metadata_json))

            # Cache the metadata json blob -- we'll probably use it
            # soon.
            cc_cache = self._crosscat_cache(bdb)
            if cc_cache is not None:
                assert generator_id not in cc_cache.metadata
                cc_cache.metadata[generator_id] = M_c

            # Expose the same information relationally.
            insert_column_sql = '''
                INSERT INTO bayesdb_crosscat_column
                    (generator_id, colno, cc_colno, disttype)
                    VALUES (:generator_id, :colno, :cc_colno, :disttype)
            '''
            insert_codemap_sql = '''
                INSERT INTO bayesdb_crosscat_column_codemap
                    (generator_id, cc_colno, code, value)
                    VALUES (:generator_id, :cc_colno, :code, :value)
            '''
            for cc_colno, (colno, name, _stattype) in enumerate(column_list):
                column_metadata = M_c['column_metadata'][cc_colno]
                disttype = column_metadata['modeltype']
                bdb.sql_execute(insert_column_sql, {
                    'generator_id': generator_id,
                    'colno': colno,
                    'cc_colno': cc_colno,
                    'disttype': disttype,
                })
                codemap = column_metadata['value_to_code']
                for code in codemap:
                    bdb.sql_execute(insert_codemap_sql, {
                        'generator_id': generator_id,
                        'cc_colno': cc_colno,
                        'code': code,
                        'value': codemap[code],
                    })

    def drop_generator(self, bdb, generator_id):
        with bdb.savepoint():
            # Remove the metadata from the cache.
            cc_cache = self._crosscat_cache_nocreate(bdb)
            if cc_cache is not None:
                if generator_id in cc_cache.metadata:
                    del cc_cache.metadata[generator_id]
                if generator_id in cc_cache.thetas:
                    del cc_cache.thetas[generator_id]

            # Delete all the things referring to the generator:
            # - models
            # - codemap
            # - columns
            # - metadata
            delete_models_sql = '''
                DELETE FROM bayesdb_crosscat_theta
                    WHERE generator_id = ?
            '''
            bdb.sql_execute(delete_models_sql, (generator_id,))
            delete_codemap_sql = '''
                DELETE FROM bayesdb_crosscat_column_codemap
                    WHERE generator_id = ?
            '''
            bdb.sql_execute(delete_codemap_sql, (generator_id,))
            delete_column_sql = '''
                DELETE FROM bayesdb_crosscat_column
                    WHERE generator_id = ?
            '''
            bdb.sql_execute(delete_column_sql, (generator_id,))
            delete_metadata_sql = '''
                DELETE FROM bayesdb_crosscat_metadata
                    WHERE generator_id = ?
            '''
            bdb.sql_execute(delete_metadata_sql, (generator_id,))

    def rename_column(self, bdb, generator_id, oldname, newname):
        assert oldname != newname
        M_c = self._crosscat_metadata(bdb, generator_id)
        assert oldname in M_c['name_to_idx']
        assert newname not in M_c['name_to_idx']
        idx = M_c['name_to_idx'][oldname]
        assert M_c['idx_to_name'][unicode(idx)] == oldname
        del M_c['name_to_idx'][oldname]
        M_c['name_to_idx'][newname] = idx
        M_c['idx_to_name'][unicode(idx)] = newname
        sql = '''
            UPDATE bayesdb_crosscat_metadata SET metadata_json = :metadata_json
                WHERE generator_id = :generator_id
        '''
        metadata_json = json.dumps(M_c)
        total_changes = bdb.sqlite3.total_changes
        bdb.sql_execute(sql, {
            'generator_id': generator_id,
            'metadata_json': metadata_json,
        })
        assert bdb.sqlite3.total_changes - total_changes == 1
        cc_cache = self._crosscat_cache_nocreate(bdb)
        if cc_cache is not None:
            cc_cache.metadata[generator_id] = M_c

    def initialize_models(self, bdb, generator_id, modelnos, model_config):
        cc_cache = self._crosscat_cache(bdb)
        if cc_cache is not None and generator_id in cc_cache.thetas:
            assert not any(modelno in cc_cache.thetas[generator_id]
                for modelno in modelnos)
        if model_config is None:
            model_config = {
                'kernel_list': (),
                'initialization': 'from_the_prior',
                'row_initialization': 'from_the_prior',
            }
        M_c = self._crosscat_metadata(bdb, generator_id)
        X_L_list, X_D_list = self._crosscat.initialize(
            M_c=M_c,
            M_r=None,           # XXX
            T=self._crosscat_data(bdb, generator_id, M_c),
            n_chains=len(modelnos),
            initialization=model_config['initialization'],
            row_initialization=model_config['row_initialization'],
        )
        if len(modelnos) == 1:  # XXX Ugh.  Fix crosscat so it doesn't do this.
            X_L_list = [X_L_list]
            X_D_list = [X_D_list]
        insert_theta_sql = '''
            INSERT INTO bayesdb_crosscat_theta
                (generator_id, modelno, theta_json)
                VALUES (:generator_id, :modelno, :theta_json)
        '''
        for modelno, (X_L, X_D) in zip(modelnos, zip(X_L_list, X_D_list)):
            theta = {
                'X_L': X_L,
                'X_D': X_D,
                'iterations': 0,
                'column_crp_alpha': [],
                'logscore': [],
                'num_views': [],
                'model_config': model_config,
            }
            bdb.sql_execute(insert_theta_sql, {
                'generator_id': generator_id,
                'modelno': modelno,
                'theta_json': json.dumps(theta),
            })
            if cc_cache is not None:
                if generator_id in cc_cache.thetas:
                    assert modelno not in cc_cache.thetas[generator_id]
                    cc_cache.thetas[generator_id][modelno] = theta
                else:
                    cc_cache.thetas[generator_id] = {modelno: theta}

    def drop_models(self, bdb, generator_id, modelnos=None):
        cc_cache = self._crosscat_cache_nocreate(bdb)
        if modelnos is None:
            if cc_cache is not None:
                if generator_id in cc_cache.thetas:
                    del cc_cache.thetas[generator_id]
            sql = '''
                DELETE FROM bayesdb_crosscat_theta WHERE generator_id = ?
            '''
            bdb.sql_execute(sql, (generator_id,))
        else:
            sql = '''
                DELETE FROM bayesdb_crosscat_theta
                    WHERE generator_id = ? AND modelno = ?
            '''
            for modelno in modelnos:
                bdb.sql_execute(sql, (generator_id, modelno))
            if cc_cache is not None and generator_id in cc_cache.thetas:
                for modelno in modelnos:
                    if modelno in thetas:
                        del cc_cache.thetas[generator_id][modelno]
                if len(cc_cache.thetas[generator_id]) == 0:
                    del cc_cache.thetas[generator_id]

    def analyze_models(self, bdb, generator_id, modelnos=None, iterations=1,
            max_seconds=None, iterations_per_checkpoint=None):
        # XXX What about a schema change or insert in the middle of
        # analysis?
        M_c = self._crosscat_metadata(bdb, generator_id)
        T = self._crosscat_data(bdb, generator_id, M_c)
        update_iterations_sql = '''
            UPDATE bayesdb_generator_model
                SET iterations = iterations + :iterations
                WHERE generator_id = :generator_id AND modelno = :modelno
        '''
        update_theta_json_sql = '''
            UPDATE bayesdb_crosscat_theta SET theta_json = :theta_json
                WHERE generator_id = :generator_id AND modelno = :modelno
        '''
        if max_seconds is not None:
            deadline = time.time() + max_seconds
        while (iterations is None or 0 < iterations) and \
              (max_seconds is None or time.time() < deadline):
            n_steps = 1
            if iterations_per_checkpoint is not None:
                assert 0 < iterations_per_checkpoint
                n_steps = iterations_per_checkpoint
            elif iterations is not None and max_seconds is None:
                n_steps = iterations
            with bdb.savepoint():
                if modelnos is None:
                    numbered_thetas = self._crosscat_thetas(bdb, generator_id)
                    update_modelnos = sorted(numbered_thetas.iterkeys())
                    thetas = [numbered_thetas[modelno] for modelno in
                        update_modelnos]
                else:
                    update_modelnos = modelnos
                    thetas = [self._crosscat_theta(bdb, generator_id, modelno)
                        for modelno in update_modelnos]
                assert 0 < len(thetas)
                X_L_list = [theta['X_L'] for theta in thetas]
                X_D_list = [theta['X_D'] for theta in thetas]
                X_L_list, X_D_list, diagnostics = self._crosscat.analyze(
                    M_c=M_c,
                    T=T,
                    do_diagnostics=True,
                    # XXX Require the models share a common kernel_list.
                    kernel_list=thetas[0]['model_config']['kernel_list'],
                    X_L=X_L_list,
                    X_D=X_D_list,
                    n_steps=n_steps,
                )
                if iterations is not None:
                    iterations -= n_steps
                cc_cache = self._crosscat_cache(bdb)
                for modelno, theta, X_L, X_D \
                        in zip(update_modelnos, thetas, X_L_list, X_D_list):
                    theta['iterations'] += n_steps
                    theta['X_L'] = X_L
                    theta['X_D'] = X_D
                    total_changes = bdb.sqlite3.total_changes
                    bdb.sql_execute(update_iterations_sql, {
                        'generator_id': generator_id,
                        'modelno': modelno,
                        'iterations': n_steps,
                    })
                    assert bdb.sqlite3.total_changes - total_changes == 1
                    total_changes = bdb.sqlite3.total_changes
                    bdb.sql_execute(update_theta_json_sql, {
                        'generator_id': generator_id,
                        'modelno': modelno,
                        'theta_json': json.dumps(theta),
                    })
                    assert bdb.sqlite3.total_changes - total_changes == 1
                    if cc_cache is not None:
                        if generator_id in cc_cache.thetas:
                            cc_cache.thetas[generator_id][modelno] = theta
                        else:
                            cc_cache.thetas[generator_id] = {modelno: theta}

    def column_dependence_probability(self, bdb, generator_id, colno0, colno1):
        if colno0 == colno1:
            return 1
        cc_colno0 = crosscat_cc_colno(bdb, generator_id, colno0)
        cc_colno1 = crosscat_cc_colno(bdb, generator_id, colno1)
        count = 0
        nmodels = 0
        for X_L, X_D in self._crosscat_latent_stata(bdb, generator_id):
            nmodels += 1
            assignments = X_L['column_partition']['assignments']
            if assignments[cc_colno0] != assignments[cc_colno1]:
                continue
            if len(unique(X_D[assignments[cc_colno0]])) <= 1:
                continue
            count += 1
        return float('NaN') if nmodels == 0 else (float(count)/float(nmodels))

    def column_mutual_information(self, bdb, generator_id, colno0, colno1,
            numsamples=None):
        if numsamples is None:
            numsamples = 100
        X_L_list = self._crosscat_latent_state(bdb, generator_id)
        X_D_list = self._crosscat_latent_data(bdb, generator_id)
        r = self._crosscat.mutual_information(
            M_c=self._crosscat_metadata(bdb, generator_id),
            X_L_list=X_L_list,
            X_D_list=X_D_list,
            Q=[(colno0, colno1)],
            n_samples=int(math.ceil(float(numsamples) / len(X_L_list)))
        )
        # r has one answer per element of Q, so take the first one.
        r0 = r[0]
        # r0 is (mi, linfoot), and we want mi.
        mi = r0[0]
        # mi is [result for model 0, result for model 1, ...], and we want
        # the mean.
        return arithmetic_mean(mi)

    def column_typicality(self, bdb, generator_id, colno):
        return self._crosscat.column_structural_typicality(
            X_L_list=self._crosscat_latent_state(bdb, generator_id),
            col_id=crosscat_cc_colno(bdb, generator_id, colno),
        )

    def column_value_probability(self, bdb, generator_id, colno, value):
        M_c = self._crosscat_metadata(bdb, generator_id)
        try:
            code = crosscat_value_to_code(bdb, generator_id, M_c, colno, value)
        except KeyError:
            return 0
        X_L_list = self._crosscat_latent_state(bdb, generator_id)
        X_D_list = self._crosscat_latent_data(bdb, generator_id)
        # Fabricate a nonexistent (`unobserved') row id.
        fake_row_id = len(X_D_list[0][0])
        cc_colno = crosscat_cc_colno(bdb, generator_id, colno)
        r = self._crosscat.simple_predictive_probability_multistate(
            M_c=M_c,
            X_L_list=X_L_list,
            X_D_list=X_D_list,
            Y=[],
            Q=[(fake_row_id, cc_colno, code)]
        )
        return math.exp(r)

    def row_similarity(self, bdb, generator_id, rowid, target_rowid, colnos):
        return self._crosscat.similarity(
            M_c=self._crosscat_metadata(bdb, generator_id),
            X_L_list=self._crosscat_latent_state(bdb, generator_id),
            X_D_list=self._crosscat_latent_data(bdb, generator_id),
            given_row_id=crosscat_row_id(rowid),
            target_row_id=crosscat_row_id(target_rowid),
            target_columns=[crosscat_cc_colno(bdb, generator_id, colno)
                for colno in colnos],
        )

    def row_typicality(self, bdb, generator_id, rowid):
        return self._crosscat.row_structural_typicality(
            X_L_list=self._crosscat_latent_state(bdb, generator_id),
            X_D_list=self._crosscat_latent_data(bdb, generator_id),
            row_id=crosscat_row_id(rowid),
        )

    def row_column_predictive_probability(self, bdb, generator_id, rowid,
            colno):
        M_c = self._crosscat_metadata(bdb, generator_id)
        table_name = core.bayesdb_generator_table(bdb, generator_id)
        colname = core.bayesdb_generator_column_name(bdb, generator_id, colno)
        qt = sqlite3_quote_name(table_name)
        qcn = sqlite3_quote_name(colname)
        value_sql = 'SELECT %s FROM %s WHERE _rowid_ = ?' % (qcn, qt)
        value_cursor = bdb.sql_execute(value_sql, (rowid,))
        value = None
        try:
            row = value_cursor.next()
        except StopIteration:
            generator = core.bayesdb_generator_name(bdb, generator_id)
            raise ValueError('No such row in %s: %d' %
                (repr(generator), rowid))
        else:
            assert len(row) == 1
            value = row[0]
        code = crosscat_value_to_code(bdb, generator_id, M_c, colno, value)
        cc_colno = crosscat_cc_colno(bdb, generator_id, colno)
        r = self._crosscat.simple_predictive_probability_multistate(
            M_c=M_c,
            X_L_list=self._crosscat_latent_state(bdb, generator_id),
            X_D_list=self._crosscat_latent_data(bdb, generator_id),
            Y=[],
            Q=[(crosscat_row_id(rowid), cc_colno, code)],
        )
        return math.exp(r)

    def infer_confidence(self, bdb, generator_id, colno, rowid,
            numsamples=None):
        if numsamples is None:
            numsamples = 1
        M_c = self._crosscat_metadata(bdb, generator_id)
        column_names = core.bayesdb_generator_column_names(bdb, generator_id)
        table_name = core.bayesdb_generator_table(bdb, generator_id)
        qt = sqlite3_quote_name(table_name)
        qcns = ','.join(map(sqlite3_quote_name, column_names))
        select_sql = ('SELECT %s FROM %s WHERE _rowid_ = ?' % (qcns, qt))
        cursor = bdb.sql_execute(select_sql, (rowid,))
        row = None
        try:
            row = cursor.next()
        except StopIteration:
            generator = core.bayesdb_generator_table(bdb, generator_id)
            raise ValueError('No such row in table %s for generator %d: %d' %
                (repr(table_name), repr(generator), repr(rowid)))
        try:
            cursor.next()
        except StopIteration:
            pass
        else:
            generator = core.bayesdb_generator_table(bdb, generator_id)
            raise ValueError('More than one such row'
                ' in table %s for generator %s: %d' %
                (repr(table_name), repr(generator), repr(rowid)))
        row_id = crosscat_row_id(rowid)
        cc_colno = crosscat_cc_colno(bdb, generator_id, colno)
        code, confidence = self._crosscat.impute_and_confidence(
            M_c=M_c,
            X_L=self._crosscat_latent_state(bdb, generator_id),
            X_D=self._crosscat_latent_data(bdb, generator_id),
            Y=[(row_id,
                crosscat_gen_colno(bdb, generator_id, cc_colno_),
                crosscat_value_to_code(bdb, generator_id, M_c,
                    crosscat_gen_colno(bdb, generator_id, cc_colno_), value))
               for cc_colno_, value in enumerate(row)
               if value is not None
               if cc_colno_ != cc_colno],
            Q=[(row_id, cc_colno)],
            n=numsamples,
        )
        value = crosscat_code_to_value(bdb, generator_id, M_c, colno, code)
        return value, confidence

    def simulate(self, bdb, generator_id, constraints, colnos,
            numpredictions=1):
        M_c = self._crosscat_metadata(bdb, generator_id)
        table_name = core.bayesdb_generator_table(bdb, generator_id)
        qt = sqlite3_quote_name(table_name)
        cursor = bdb.sql_execute('SELECT MAX(_rowid_) FROM %s' % (qt,))
        max_rowid = None
        try:
            row = cursor.next()
        except StopIteration:
            assert False, 'SELECT MAX(rowid) returned no results!'
        else:
            assert len(row) == 1
            max_rowid = row[0]
        fake_rowid = max_rowid + 1
        fake_row_id = crosscat_row_id(fake_rowid)
        # XXX Why special-case empty constraints?
        Y = None
        if constraints is not None:
            Y = [(fake_row_id, colno,
                  crosscat_value_to_code(bdb, generator_id, M_c, colno, value))
                 for colno, value in constraints]
        raw_outputs = self._crosscat.simple_predictive_sample(
            M_c=M_c,
            X_L=self._crosscat_latent_state(bdb, generator_id),
            X_D=self._crosscat_latent_data(bdb, generator_id),
            Y=Y,
            Q=[(fake_row_id, crosscat_cc_colno(bdb, generator_id, colno))
                for colno in colnos],
            n=numpredictions
        )
        return [[crosscat_code_to_value(bdb, generator_id, M_c, colno, code)
                for (colno, code) in zip(colnos, raw_output)]
            for raw_output in raw_outputs]

    def insertmany(self, bdb, generator_id, rows):
        with bdb.savepoint():
            # Insert the data into the table.
            table_name = core.bayesdb_generator_table(bdb, generator_id)
            qt = sqlite3_quote_name(table_name)
            sql_column_names = core.bayesdb_table_column_names(bdb, table_name)
            qcns = map(sqlite3_quote_name, sql_column_names)
            sql = '''
                INSERT INTO %s (%s) VALUES (%s)
            ''' % (qt, ', '.join(qcns), ', '.join('?' for _qcn in qcns))
            for row in rows:
                if len(row) != len(sql_column_names):
                    raise ValueError('Wrong row length'
                        ': expected %d, got %d'
                        (len(sql_column_names), len(row)))
                bdb.sql_execute(sql, row)

            # Find the indices of the modelled columns.
            # XXX Simplify this -- we have the correspondence between
            # colno and modelled_colno in the database.
            modelled_column_names = \
                core.bayesdb_generator_column_names(bdb, generator_id)
            remap = []
            for i, name in enumerate(sql_column_names):
                colno = len(remap)
                if len(modelled_column_names) <= colno:
                    break
                if casefold(name) == casefold(modelled_column_names[colno]):
                    remap.append(i)
            assert len(remap) == len(modelled_column_names)
            M_c = self._crosscat_metadata(bdb, generator_id)
            modelled_rows = [[crosscat_value_to_code(bdb, generator_id, M_c,
                        colno, row[i])
                    for colno, i in enumerate(remap)]
                for row in rows]

            # Update the models.
            T = self._crosscat_data(bdb, generator_id, M_c)
            models_sql = '''
                SELECT m.modelno, ct.theta_json
                    FROM bayesdb_generator_model AS m,
                        bayesdb_crosscat_theta AS ct
                    WHERE m.generator_id = ?
                        AND m.generator_id = ct.generator_id
                        AND m.modelno = ct.modelno
                    ORDER BY m.modelno
            '''
            models = list(bdb.sql_execute(models_sql, (generator_id,)))
            modelnos = [modelno for modelno, _theta_json in models]
            thetas = [json.loads(theta_json)
                for _modelno, theta_json in models]
            X_L_list, X_D_list, T = self._crosscat.insert(
                M_c=M_c,
                T=T,
                X_L_list=[theta['X_L'] for theta in thetas],
                X_D_list=[theta['X_D'] for theta in thetas],
                new_rows=modelled_rows,
            )
            assert T == self._crosscat_data(bdb, generator_id, M_c) \
                + modelled_rows
            update_theta_sql = '''
                UPDATE bayesdb_crosscat_theta SET theta_json = :theta_json
                    WHERE generator_id = :generator_id AND modelno = :modelno
            '''
            for modelno, theta, X_L, X_D \
                    in zip(modelnos, thetas, X_L_list, X_D_list):
                theta['X_L'] = X_L
                theta['X_D'] = X_D
                total_changes = bdb.sqlite3.total_changes
                bdb.sql_execute(update_theta_sql, {
                    'generator_id': generator_id,
                    'modelno': modelno,
                    'theta_json': json.dumps(theta),
                })
                assert bdb.sqlite3.total_changes - total_changes == 1

class CrosscatCache(object):
    def __init__(self):
        self.metadata = {}
        self.thetas = {}

def create_metadata(bdb, generator_id, column_list):
    ncols = len(column_list)
    column_names = [name for _colno, name, _stattype in column_list]
    column_metadata = [metadata_creators[casefold(stattype)](bdb, generator_id,
            colno)
        for colno, _name, stattype in column_list]
    return {
        'name_to_idx': dict(zip(column_names, range(ncols))),
        'idx_to_name': dict(zip(map(unicode, range(ncols)), column_names)),
        'column_metadata': column_metadata,
    }

def create_metadata_numerical(_bdb, _generator_id, _colno):
    return {
        'modeltype': 'normal_inverse_gamma',
        'value_to_code': {},
        'code_to_value': {},
    }

def create_metadata_cyclic(_bdb, _generator_id, _colno):
    return {
        'modeltype': 'vonmises',
        'value_to_code': {},
        'code_to_value': {},
    }

def create_metadata_ignore(bdb, generator_id, colno):
    metadata = create_metadata_categorical(bdb, generator_id, colno)
    metadata['modeltype'] = 'ignore'
    return metadata

def create_metadata_key(bdb, table, colno):
    metadata = create_metadata_categorical(bdb, generator_id, colno)
    metadata['modeltype'] = 'key'
    return metadata

def create_metadata_categorical(bdb, generator_id, colno):
    table = core.bayesdb_generator_table(bdb, generator_id)
    column_name = core.bayesdb_table_column_name(bdb, table, colno)
    qt = sqlite3_quote_name(table)
    qcn = sqlite3_quote_name(column_name)
    sql = '''
        SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL ORDER BY %s
    ''' % (qcn, qt, qcn, qcn)
    cursor = bdb.sql_execute(sql)
    codes = [row[0] for row in cursor]
    ncodes = len(codes)
    return {
        'modeltype': 'symmetric_dirichlet_discrete',
        'value_to_code': dict(zip(range(ncodes), codes)),
        'code_to_value': dict(zip(codes, range(ncodes))),
    }

metadata_creators = {
    'categorical': create_metadata_categorical,
    'cyclic': create_metadata_cyclic,
    'ignore': create_metadata_ignore,   # XXX Why any metadata here?
    'key': create_metadata_key,         # XXX Why any metadata here?
    'numerical': create_metadata_numerical,
}

def crosscat_row_id(rowid):
    return rowid - 1

def crosscat_value_to_code(bdb, generator_id, M_c, colno, value):
    stattype = core.bayesdb_generator_column_stattype(bdb, generator_id, colno)
    if stattype == 'categorical':
        # For hysterical raisins, code_to_value and value_to_code are
        # backwards.
        #
        # XXX Fix this.
        if value is None:
            return float('NaN')         # XXX !?!??!
        cc_colno = crosscat_cc_colno(bdb, generator_id, colno)
        key = unicode(value)
        code = M_c['column_metadata'][cc_colno]['code_to_value'][key]
        # XXX Crosscat expects floating-point codes.
        return float(code)
    elif stattype in ('cyclic', 'numerical'):
        # Data may be stored in the SQL table as strings, if imported
        # from wacky sources like CSV files, in which case both NULL
        # and non-numerical data -- including the string `nan' which
        # makes sense, and anything else which doesn't -- will be
        # represented by NaN.
        try:
            return float(value)
        except (ValueError, TypeError):
            return float('NaN')
    else:
        raise KeyError

def crosscat_code_to_value(bdb, generator_id, M_c, colno, code):
    stattype = core.bayesdb_generator_column_stattype(bdb, generator_id, colno)
    if stattype == 'categorical':
        if math.isnan(code):
            return None
        cc_colno = crosscat_cc_colno(bdb, generator_id, colno)
        # XXX Whattakludge.
        key = unicode(int(code))
        return M_c['column_metadata'][cc_colno]['value_to_code'][key]
    elif stattype in ('cyclic', 'numerical'):
        if math.isnan(code):
            return None
        return code
    else:
        raise KeyError

def crosscat_cc_colno(bdb, generator_id, colno):
    sql = '''
        SELECT cc_colno FROM bayesdb_crosscat_column
            WHERE generator_id = ? AND colno = ?
    '''
    cursor = bdb.sql_execute(sql, (generator_id, colno))
    try:
        row = cursor.next()
    except StopIteration:
        generator = core.bayesdb_generator_name(bdb, generator_id)
        colname = core.bayesdb_generator_column_name(bdb, generator_id, colno)
        raise ValueError('Column not modelled in generator %s: %s' %
            (repr(generator), repr(colname)))
    else:
        assert len(row) == 1
        assert isinstance(row[0], int)
        return row[0]

def crosscat_gen_colno(bdb, generator_id, cc_colno):
    sql = '''
        SELECT colno FROM bayesdb_crosscat_column
            WHERE generator_id = ? AND cc_colno = ?
    '''
    cursor = bdb.sql_execute(sql, (generator_id, cc_colno))
    try:
        row = cursor.next()
    except StopIteration:
        generator = core.bayesdb_generator_name(bdb, generator_id)
        colname = core.bayesdb_generator_column_name(bdb, generator_id, colno)
        raise ValueError('Column not Crosscat-modelled in generator %s: %s' %
            (repr(generator), repr(colname)))
    else:
        assert len(row) == 1
        assert isinstance(row[0], int)
        return row[0]
