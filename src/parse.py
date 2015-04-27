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

import StringIO

import bayeslite.ast as ast
import bayeslite.grammar as grammar
import bayeslite.scan as scan

class BQLParseError(Exception):
    def __init__(self, errors):
        assert 0 < len(errors)
        self.errors = errors
    def __str__(self):
        if len(self.errors) == 1:
            return 'Parse error: ' + self.errors[0]
        else:
            out = StringIO.StringIO()
            out.write('Parse errors:\n')
            for error in self.errors:
                out.write('  %s\n' % (error,))
            return out.getvalue()

def parse_bql_phrases(scanner):
    semantics = BQLSemantics()
    parser = grammar.Parser(semantics)
    while not semantics.failed:
        token = scanner.read()
        if token[0] == -1:      # error
            semantics.syntax_error(token)
        else:
            if token[0] == 0:   # EOF
                # Implicit ; at EOF.
                parser.feed((grammar.T_SEMI, ';'))
            parser.feed(token)
        if semantics.phrase is not None:
            phrase = semantics.phrase
            semantics.phrase = None
            if 0 < len(semantics.errors):
                # Keep parsing in order to detect more errors, but
                # don't yield any broken phrases in case the caller
                # will try to process them before we finish parsing
                # the whole thing.
                continue
            if 0 < scanner.n_numpar:
                n_numpar = scanner.n_numpar
                nampar_map = scanner.nampar_map
                yield ast.Parametrized(phrase, n_numpar, nampar_map)
            else:
                yield phrase
        if token[0] == 0:       # EOF
            break
    if 0 < len(semantics.errors):
        raise BQLParseError(semantics.errors)
    if semantics.failed:
        raise BQLParseError(['parse failed mysteriously!'])

def parse_bql_string_pos(string):
    scanner = scan.BQLScanner(StringIO.StringIO(string), '(string)')
    phrases = parse_bql_phrases(scanner)
    # XXX Don't dig out internals of scanner: fix plex to have a
    # public API for finding the current position.
    return ((phrase, scanner.cur_pos) for phrase in phrases)

def parse_bql_string_pos_1(string):
    for phrase, pos in parse_bql_string_pos(string):
        return (phrase, pos)
    return None

def parse_bql_string(string):
    return (phrase for phrase, _pos in parse_bql_string_pos(string))

def bql_string_complete_p(string):
    scanner = scan.BQLScanner(StringIO.StringIO(string), '(string)')
    semantics = BQLSemantics()
    parser = grammar.Parser(semantics)
    nonsemi = False
    while not semantics.failed:
        token = scanner.read()
        if token[0] == -1:      # error
            # Say it's complete so the caller will try to parse it and
            # choke on the error.
            return True
        elif token[0] == 0:
            # EOF.  Hope we have a complete phrase.
            break
        elif token[0] != grammar.T_SEMI:
            # Got a non-semicolon token.  Clear any previous phrase,
            # if we had one.
            nonsemi = True
            semantics.phrase = None
        parser.feed(token)
    if 0 < len(semantics.errors):
        return True
    if semantics.failed:
        return True
    return (not nonsemi) or (semantics.phrase is not None)

class BQLSemantics(object):
    def __init__(self):
        self.phrase = None
        self.errors = []
        self.failed = False

    def accept(self):
        pass
    def parse_failed(self):
        self.failed = True
    def syntax_error(self, (number, text)):
        # XXX Adapt lemonade to help us identify what the allowed
        # subsequent tokens are.
        #
        # XXX Record source position.
        if number == -1:        # error
            self.errors.append('skipping bad token: %s' % (text,))
        else:
            self.errors.append('syntax error near %s' % (text,))

    def p_bql_start(self, phrases):
        pass
    def p_phrases_none(self):
        pass
    def p_phrases_some(self, phrases, phrase):
        pass

    def p_phrase_opt_none(self):
        pass
    def p_phrase_opt_some(self, phrase):
        assert self.phrase is None
        self.phrase = phrase

    def p_phrase_command(self, c):              return c
    def p_phrase_query(self, q):                return q

    # Transactions
    def p_command_begin(self):                  return ast.Begin()
    def p_command_rollback(self):               return ast.Rollback()
    def p_command_commit(self):                 return ast.Commit()

    # SQL Data Definition Language subset
    def p_command_createtab_as(self, temp, ifnotexists, name, query):
        return ast.CreateTabAs(temp, ifnotexists, name, query)
    def p_command_createtab_sim(self, temp, ifnotexists, name, query):
        return ast.CreateTabSim(temp, ifnotexists, name, query)
    def p_command_droptab(self, ifexists, name):
        return ast.DropTab(ifexists, name)
    def p_command_altertab(self, table, cmds):
        return ast.AlterTab(table, cmds)

    def p_altertab_cmds_one(self, cmd):         return [cmd]
    def p_altertab_cmds_many(self, cmds, cmd):  cmds.append(cmd); return cmds
    def p_altertab_cmd_renametab(self, name):
        return ast.AlterTabRenameTab(name)
    def p_altertab_cmd_renamecol(self, old, new):
        return ast.AlterTabRenameCol(old, new)
    def p_altertab_cmd_setdefgen(self, generator):
        return ast.AlterTabSetDefGen(generator)
    def p_altertab_cmd_unsetdefgen(self):
        return ast.AlterTabSetDefGen()

    # BQL Model Definition Language
    def p_command_creategen(self, defaultp, name, ifnotexists, table,
            metamodel, schema):
        return ast.CreateGen(defaultp, name, ifnotexists, table,
            metamodel, schema)
    def p_command_dropgen(self, ifexists, name):
        return ast.DropGen(ifexists, name)
    def p_command_altergen(self, generator, cmds):
        return ast.AlterGen(generator, cmds)

    def p_default_opt_none(self):               return False
    def p_default_opt_some(self):               return True

    def p_altergen_cmds_one(self, cmd):         return [cmd]
    def p_altergen_cmds_many(self, cmds, cmd):  cmds.append(cmd); return cmds
    def p_altergen_cmd_renamegen(self, name):
        return ast.AlterGenRenameGen(name)

    def p_generator_schema_one(self, s):        return [s]
    def p_generator_schema_many(self, ss, s):   ss.append(s); return ss
    def p_generator_schemum_empty(self):        return []
    def p_generator_schemum_nonempty(self, s, t): s.append(t); return s
    def p_gs_token_prim(self, t):               return t
    def p_gs_token_comp(self, s):               return s

    def p_stattype_s(self, name):
        return name

    # BQL Model Analysis Language
    def p_command_init_models(self, n, ifnotexists, generator):
        # XXX model config
        return ast.InitModels(ifnotexists, generator, n, config=None)
    def p_command_analyze_models(self, generator, models, anlimit, anckpt,
            wait):
        iterations = anlimit[1] if anlimit[0] == 'iterations' else None
        seconds = anlimit[1] if anlimit[0] == 'seconds' else None
        return ast.AnalyzeModels(generator, models, iterations, seconds,
            anckpt, wait)
    def p_command_drop_models(self, models, generator):
        return ast.DropModels(generator, models)

    def p_temp_opt_none(self):                  return False
    def p_temp_opt_some(self):                  return True
    def p_ifexists_none(self):                  return False
    def p_ifexists_some(self):                  return True
    def p_ifnotexists_none(self):               return False
    def p_ifnotexists_some(self):               return True

    def p_anmodelset_opt_none(self):            return None
    def p_anmodelset_opt_some(self, m):         return sorted(m)
    def p_modelset_opt_none(self):              return None
    def p_modelset_opt_some(self, m):           return sorted(m)
    def p_modelset_one(self, r):                return r
    def p_modelset_many(self, m, r):            m += r; return m
    def p_modelrange_single(self, modelno):     return [modelno]
    def p_modelrange_multi(self, minno, maxno): return range(minno, maxno + 1)

    def p_anlimit_iterations(self, n):          return ('iterations', n)
    def p_anlimit_minutes(self, n):             return ('seconds', 60*n)
    def p_anlimit_seconds(self, n):             return ('seconds', n)

    def p_anckpt_none(self):                    return None
    def p_anckpt_niters(self, n):               return n

    def p_wait_opt_none(self):                  return False
    def p_wait_opt_some(self):                  return True

    def p_simulate_s(self, cols, generator, constraints, lim):
        return ast.Simulate(cols, generator, constraints, lim.limit)
    def p_simulate_nolimit(self, cols, generator, constraints):
        # XXX Report source location.
        self.errors.append('simulate missing limit')
        return ast.Simulate(cols, generator, constraints, 0)
    def p_simulate_columns_one(self, col):
        return [col]
    def p_simulate_columns_many(self, cols, col):
        cols.append(col)
        return cols
    def p_given_opt_none(self):                 return []
    def p_given_opt_some(self, constraints):    return constraints
    def p_constraints_one(self, c):             return [c]
    def p_constraints_many(self, cs, c):        cs.append(c); return cs
    def p_constraint_c(self, col, value):       return (col, value)

    def p_query_action_none(self):              return None
    def p_query_action_freq(self):              return ast.QACT_FREQ
    def p_query_action_hist(self):              return ast.QACT_HIST
    def p_query_action_summarize(self):         return ast.QACT_SUMMARIZE
    def p_query_action_plot(self):              return ast.QACT_PLOT

    def p_query_select(self, q):                return q
    def p_query_estimate(self, q):              return q
    def p_query_estcols(self, q):               return q
    def p_query_estpaircols(self, q):           return q
    def p_query_estpairrow(self, q):            return q
    def p_query_infer(self, q):                 return q
    def p_query_estimate_pairwise_row(self, q): return q
    def p_query_create_column_list(self, q):    return q

    def p_select_s(self, quant, cols, tabs, cond, grouping, ord, lim):
        return ast.Select(quant, cols, tabs, cond, grouping, ord, lim)

    def p_estimate_e(self, quant, cols, generator, cond, grouping, ord, lim):
        return ast.Estimate(quant, cols, generator, cond, grouping, ord, lim)

    def p_estimate_columns_one(self, c):        return [c]
    def p_estimate_columns_many(self, cs, c):   cs.append(c); return cs
    def p_estimate_column_sel(self, c):         return c
    def p_estimate_column_inf(self, col, name, confname):
        return ast.InfCol(col, name, confname)

    def p_estcols_nocols(self, generator, cond, ord, lim):
        return ast.EstCols([], generator, cond, ord, lim)
    def p_estcols_cols(self, cols, generator, cond, ord, lim):
        return ast.EstCols(cols, generator, cond, ord, lim)

    def p_estcols_columns_opt_none(self):       return []
    def p_estcols_columns_opt_some(self, cols): return cols
    def p_estcols_columns_one(self, col):       return [col]
    def p_estcols_columns_many(self, cols, col): cols.append(col); return cols
    def p_estcols_column_ec(self, e, name):     return (e, name)

    def p_estpaircols_e(self, cols, generator, subcols, cond, ord, lim):
        return ast.EstPairCols(cols, generator, subcols, cond, ord, lim)

    def p_estpaircols_columns_one(self, col):
        return [col]
    def p_estpaircols_columns_many(self, cols, col):
        cols.append(col); return cols
    def p_estpaircols_column_epc(self, e, name):
        return (e, name)

    def p_estpairrow_e(self, e, generator, cond, ord, lim):
        return ast.EstPairRow(e, generator, cond, ord, lim)

    def p_select_quant_distinct(self):          return ast.SELQUANT_DISTINCT
    def p_select_quant_all(self):               return ast.SELQUANT_ALL
    def p_select_quant_default(self):           return ast.SELQUANT_ALL

    def p_select_columns_one(self, c):          return [c]
    def p_select_columns_many(self, cs, c):     cs.append(c); return cs

    def p_select_column_star(self):             return ast.SelColAll(None)
    def p_select_column_qstar(self, table):     return ast.SelColAll(table)
    def p_select_column_qsub(self, table, q):   return ast.SelColSub(table, q)
    def p_select_column_exp(self, e, name):     return ast.SelColExp(e, name)

    def p_as_none(self):                        return None
    def p_as_some(self, name):                  return name

    def p_from_empty(self):                     return None
    def p_from_nonempty(self, tables):          return tables

    def p_select_tables_one(self, t):           return [t]
    def p_select_tables_many(self, ts, t):      ts.append(t); return ts
    def p_select_table_named(self, table, name): return ast.SelTab(table, name)
    def p_select_table_subquery(self, q, name):  return ast.SelTab(q, name)

    def p_for_none(self):                       return None
    def p_for_one(self, collist):               return collist

    def p_where_unconditional(self):            return None
    def p_where_conditional(self, condition):   return condition

    def p_column_name_cn(self, name):           return name
    def p_generator_name_unqualified(self, name): return name
    def p_metamodel_name_mn(self, name):        return name
    def p_table_name_unqualified(self, name):   return name

    def p_group_by_none(self):                  return None
    def p_group_by_some(self, keys):            return ast.Grouping(keys, None)
    def p_group_by_having(self, keys, cond):    return ast.Grouping(keys, cond)

    def p_order_by_none(self):                  return None
    def p_order_by_some(self, keys):            return keys
    def p_order_keys_one(self, key):            return [key]
    def p_order_keys_many(self, keys, key):     keys.append(key); return keys
    def p_order_key_k(self, e, s):              return ast.Ord(e, s)
    def p_order_sense_none(self):               return ast.ORD_ASC
    def p_order_sense_asc(self):                return ast.ORD_ASC
    def p_order_sense_desc(self):               return ast.ORD_DESC

    def p_limit_opt_none(self):                 return None
    def p_limit_opt_some(self, lim):            return lim

    def p_limit_n(self, limit):                 return ast.Lim(limit, None)
    def p_limit_offset(self, limit, offset):    return ast.Lim(limit, offset)
    def p_limit_comma(self, offset, limit):     return ast.Lim(limit, offset)

    def p_expressions_opt_none(self):           return []
    def p_expressions_opt_some(self, es):       return es

    def p_expressions_one(self, e):             return [e]
    def p_expressions_many(self, es, e):        es.append(e); return es

    def p_expression_top(self, e):      return e
    def p_bqlfn0_exp(self, e):          return e
    def p_expression1_top(self, e):     return e

    def p_boolean_or_or(self, l, r):    return ast.op(ast.OP_BOOLOR, l, r)
    def p_boolean_or_and(self, a):      return a
    def p_boolean_and_and(self, l, r):  return ast.op(ast.OP_BOOLAND, l, r)
    def p_boolean_and_not(self, n):     return n
    def p_boolean_not_not(self, n):     return ast.op(ast.OP_BOOLNOT, n)
    def p_boolean_not_equality(self, c):
                                        return c
    def p_equality_is(self, l, r):      return ast.op(ast.OP_IS, l, r)
    def p_equality_isnot(self, l, r):   return ast.op(ast.OP_ISNOT, l, r)
    def p_equality_like(self, l, r):    return ast.op(ast.OP_LIKE, l, r)
    def p_equality_notlike(self, l, r): return ast.op(ast.OP_NOTLIKE, l, r)
    def p_equality_like_esc(self, l, r, e):
                                        return ast.op(ast.OP_LIKE_ESC, l, r, e)
    def p_equality_notlike_esc(self, l, r, e):
                                        return ast.op(ast.OP_NOTLIKE_ESC,
                                            l, r, e)
    def p_equality_glob(self, l, r):    return ast.op(ast.OP_GLOB, l, r)
    def p_equality_notglob(self, l, r): return ast.op(ast.OP_NOTGLOB, l, r)
    def p_equality_glob_esc(self, l, r, e):
                                        return ast.op(ast.OP_GLOB_ESC, l, r, e)
    def p_equality_notglob_esc(self, l, r, e):
                                        return ast.op(ast.OP_NOTGLOB_ESC,
                                            l, r, e)
    def p_equality_regexp(self, l, r):  return ast.op(ast.OP_REGEXP, l, r)
    def p_equality_notregexp(self, l, r):
                                        return ast.op(ast.OP_NOTREGEXP, l, r)
    def p_equality_regexp_esc(self, l, r, e):
                                        return ast.op(ast.OP_REGEXP_ESC,
                                            l, r, e)
    def p_equality_notregexp_esc(self, l, r, e):
                                        return ast.op(ast.OP_NOTREGEXP_ESC,
                                            l, r, e)
    def p_equality_match(self, l, r):   return ast.op(ast.OP_MATCH, l, r)
    def p_equality_notmatch(self, l, r):
                                        return ast.op(ast.OP_NOTMATCH, l, r)
    def p_equality_match_esc(self, l, r, e):
                                        return ast.op(ast.OP_MATCH_ESC, l, r, e)
    def p_equality_notmatch_esc(self, l, r, e):
                                        return ast.op(ast.OP_NOTMATCH_ESC,
                                            l, r, e)
    def p_equality_between(self, m, l, r):
                                        return ast.op(ast.OP_BETWEEN, m, l, r)
    def p_equality_notbetween(self, m, l, r):
                                        return ast.op(ast.OP_NOTBETWEEN, m,l,r)
    def p_equality_in(self, e, q):      return ast.ExpIn(e, True, q)
    def p_equality_notin(self, e, q):   return ast.ExpIn(e, False, q)
    def p_equality_isnull(self, e):     return ast.op(ast.OP_ISNULL, e)
    def p_equality_notnull(self, e):    return ast.op(ast.OP_NOTNULL, e)
    def p_equality_neq(self, l, r):     return ast.op(ast.OP_NEQ, l, r)
    def p_equality_eq(self, l, r):      return ast.op(ast.OP_EQ, l, r)
    def p_equality_ordering(self, o):   return o
    def p_ordering_lt(self, l, r):      return ast.op(ast.OP_LT, l, r)
    def p_ordering_leq(self, l, r):     return ast.op(ast.OP_LEQ, l, r)
    def p_ordering_geq(self, l, r):     return ast.op(ast.OP_GEQ, l, r)
    def p_ordering_gt(self, l, r):      return ast.op(ast.OP_GT, l, r)
    def p_ordering_bitwise(self, b):    return b
    def p_bitwise_and(self, l, r):      return ast.op(ast.OP_BITAND, l, r)
    def p_bitwise_ior(self, l, r):      return ast.op(ast.OP_BITIOR, l, r)
    def p_bitwise_lshift(self, l, r):   return ast.op(ast.OP_LSHIFT, l, r)
    def p_bitwise_rshift(self, l, r):   return ast.op(ast.OP_RSHIFT, l, r)
    def p_bitwise_additive(self, a):    return a
    def p_additive_add(self, l, r):     return ast.op(ast.OP_ADD, l, r)
    def p_additive_sub(self, l, r):     return ast.op(ast.OP_SUB, l, r)
    def p_additive_mult(self, m):       return m
    def p_multiplicative_mul(self, l, r):
                                        return ast.op(ast.OP_MUL, l, r)
    def p_multiplicative_div(self, l, r):
                                        return ast.op(ast.OP_DIV, l, r)
    def p_multiplicative_rem(self, l, r):
                                        return ast.op(ast.OP_REM, l, r)
    def p_multiplicative_conc(self, c): return c
    def p_concatenative_concat(self, l, r):
                                        return ast.op(ast.OP_CONCAT, l, r)
    def p_concatenative_collate(self, c):
                                        return c
    def p_collating_collate(self, e, c):
                                        return ast.ExpCollate(e, c)
    def p_collating_bitwise_not(self, n):
                                        return n
    def p_bitwise_not_not(self, n):     return ast.op(ast.OP_BITNOT, n)
    def p_bitwise_not_bql(self, b):     return b

    def p_bqlfn_predprob_row(self, col):        return ast.ExpBQLPredProb(col)
    def p_bqlfn_prob_const(self, col, e):       return ast.ExpBQLProb(col, e)
    def p_bqlfn_prob_1col(self, e):             return ast.ExpBQLProb(None, e)
    def p_bqlfn_typ_1col_or_row(self):          return ast.ExpBQLTyp(None)
    def p_bqlfn_typ_const(self, col):           return ast.ExpBQLTyp(col)
    def p_bqlfn_sim_1row(self, cond, cols):     return ast.ExpBQLSim(cond,cols)
    def p_bqlfn_sim_2row(self, cols):           return ast.ExpBQLSim(None,cols)
    def p_bqlfn_depprob(self, cols):            return ast.ExpBQLDepProb(*cols)
    def p_bqlfn_mutinf(self, cols, nsamp):
        return ast.ExpBQLMutInf(cols[0], cols[1], nsamp)
    def p_bqlfn_correl(self, cols):             return ast.ExpBQLCorrel(*cols)
    def p_bqlfn_infer(self, col, cf):           return ast.ExpBQLInfer(col, cf)
    def p_bqlfn_primary(self, p):               return p

    def p_wrt_none(self):                       return [ast.ColListAll()]
    def p_wrt_one(self, collist):               return [collist]
    def p_wrt_some(self, collists):             return collists

    def p_ofwith_bql_2col(self):                return (None, None)
    def p_ofwith_bql_1col(self, col):           return (col, None)
    def p_ofwith_bql_const(self, col1, col2):   return (col1, col2)

    def p_nsamples_opt_none(self):              return None
    def p_nsamples_opt_some(self, nsamples):    return nsamples

    def p_column_lists_one(self, collist):
        return [collist]
    def p_column_lists_many(self, collists, collist):
        collists.append(collist)
        return collists

    def p_column_list_all(self):                return ast.ColListAll()
    def p_column_list_column(self, col):        return ast.ColListLit([col])
    def p_column_list_subquery(self, q):        return ast.ColListSub(q)

    def p_primary_literal(self, v):             return ast.ExpLit(v)
    def p_primary_numpar(self, n):              return ast.ExpNumpar(n)
    def p_primary_nampar(self, n):              return ast.ExpNampar(n[0],n[1])
    def p_primary_apply(self, fn, es):          return ast.ExpApp(False,fn,es)
    def p_primary_apply_distinct(self, fn, es): return ast.ExpApp(True, fn, es)
    def p_primary_apply_star(self, fn):         return ast.ExpAppStar(fn)
    def p_primary_paren(self, e):               return e
    def p_primary_subquery(self, q):            return ast.ExpSub(q)
    def p_primary_cast(self, e, t):             return ast.ExpCast(e, t)
    def p_primary_exists(self, q):              return ast.ExpExists(q)
    def p_primary_column(self, col):            return ast.ExpCol(None, col)
    def p_primary_tabcol(self, tab, col):       return ast.ExpCol(tab, col)
    def p_primary_case(self, k, ws, e):         return ast.ExpCase(k, ws, e)

    def p_case_key_opt_none(self):              return None
    def p_case_key_opt_some(self, k):           return k
    def p_case_whens_opt_none(self):            return []
    def p_case_whens_opt_some(self, ws, w, t):  ws.append((w, t)); return ws
    def p_case_else_opt_none(self):             return None
    def p_case_else_opt_some(self, e):          return e

    def p_literal_null(self):                   return ast.LitNull(None)
    def p_literal_integer(self, i):             return ast.LitInt(i)
    def p_literal_float(self, f):               return ast.LitFloat(f)
    def p_literal_string(self, s):              return ast.LitString(s)

    def p_type_name(self, n):                   return ast.Type(n, [])
    def p_type_onearg(self, n, a):              return ast.Type(n, [a])
    def p_type_twoarg(self, n, a, b):           return ast.Type(n, [a, b])
    def p_typename_one(self, n):                return [n]
    def p_typename_many(self, tn, n):           tn.append(n); return tn
    def p_typearg_unsigned(self, i):            return i
    def p_typearg_positive(self, i):            return i
    def p_typearg_negative(self, i):            return -i
