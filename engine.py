"""Bounded read-only SQLite experiments over reproducible synthetic fixtures."""
from collections import Counter
from datetime import date, timedelta
import json
import math
import random
import re
import sqlite3
import statistics
import time

INDEXES = {
    'customer': 'CREATE INDEX idx_orders_customer ON orders(customer_id, created_at, id, amount_cents)',
    'status_date': 'CREATE INDEX idx_orders_status_date ON orders(status, created_at, id, amount_cents)',
    'amount': 'CREATE INDEX idx_orders_amount ON orders(amount_cents DESC, id)',
}
SCHEMA = '''
CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL);
CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL, status TEXT NOT NULL, amount_cents INTEGER NOT NULL);
'''
FUNCTIONS = {'abs','avg','count','sum','total','min','max','round','coalesce','ifnull','nullif',
             'lower','upper','length','substr','substring','trim','ltrim','rtrim','replace',
             'date','datetime','strftime','julianday','unixepoch','like','glob','typeof',
             'row_number','rank','dense_rank','lag','lead'}


def fixture():
    rng = random.Random(202611)
    regions = ['North','South','East','West']
    customers = [(i,f'Customer {i:04d}',regions[(i-1)%4]) for i in range(1,1001)]
    orders = [(i,rng.randint(1,1000),(date(2025,1,1)+timedelta(days=rng.randrange(365))).isoformat(),
               rng.choice(['new','paid','shipped','cancelled']),rng.randint(100,100000)) for i in range(1,6001)]
    return customers, orders


CUSTOMERS, ORDERS = fixture()


def validate(data):
    if not isinstance(data,dict) or set(data) != {'sql','parameters','indexes'}:
        raise ValueError('Provide sql, parameters, and indexes only.')
    sql, parameters, indexes = data['sql'], data['parameters'], data['indexes']
    if not isinstance(sql,str) or not sql.strip() or len(sql)>4000 or '\x00' in sql:
        raise ValueError('SQL must contain 1–4,000 characters without NUL.')
    if not isinstance(parameters,dict) or len(parameters)>20:
        raise ValueError('Provide at most 20 named parameters.')
    for key,value in parameters.items():
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,31}',key):
            raise ValueError('Parameter names must be simple identifiers without a colon.')
        if value is None:
            continue
        if type(value) is int and -(2**63)<=value<2**63:
            continue
        if type(value) is float and math.isfinite(value):
            continue
        if isinstance(value,str) and len(value)<=200:
            continue
        raise ValueError('Parameters must be null, bounded strings, finite numbers, or signed 64-bit integers.')
    if not isinstance(indexes,list) or any(not isinstance(i,str) or i not in INDEXES for i in indexes) or len(set(indexes))!=len(indexes):
        raise ValueError('Choose unique known index names.')
    return {'sql':sql,'parameters':dict(parameters),'indexes':list(indexes)}


def database(indexes):
    conn=sqlite3.connect(':memory:',cached_statements=0)
    conn.execute('PRAGMA automatic_index=OFF')
    conn.executescript(SCHEMA)
    conn.executemany('INSERT INTO customers VALUES(?,?,?)',CUSTOMERS)
    conn.executemany('INSERT INTO orders VALUES(?,?,?,?,?)',ORDERS)
    conn.commit()
    page_size=conn.execute('PRAGMA page_size').fetchone()[0]
    before=conn.execute('PRAGMA page_count').fetchone()[0]
    started=time.perf_counter()
    for name in indexes:
        conn.execute(INDEXES[name])
    build_ms=(time.perf_counter()-started)*1000
    after=conn.execute('PRAGMA page_count').fetchone()[0]
    conn.execute('ANALYZE')
    conn.execute('PRAGMA query_only=ON')
    for category,limit in [(sqlite3.SQLITE_LIMIT_LENGTH,16384),(sqlite3.SQLITE_LIMIT_SQL_LENGTH,8192),
                           (sqlite3.SQLITE_LIMIT_COLUMN,32),(sqlite3.SQLITE_LIMIT_EXPR_DEPTH,50),
                           (sqlite3.SQLITE_LIMIT_COMPOUND_SELECT,10),(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,32),
                           (sqlite3.SQLITE_LIMIT_ATTACHED,0)]:
        conn.setlimit(category,limit)
    return conn, {'build_ms':build_ms,'allocated_index_bytes':(after-before)*page_size}


def authorize(action,first,second,_database,_source):
    if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    if action==sqlite3.SQLITE_READ and first in ('customers','orders'):
        return sqlite3.SQLITE_OK
    if action==sqlite3.SQLITE_FUNCTION and second in FUNCTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def experiment(sql,parameters,indexes,step_limit=300000):
    conn,storage=database(indexes)
    conn.set_authorizer(authorize)
    instructions=0
    deadline=time.perf_counter()+0.5

    def progress():
        nonlocal instructions
        instructions+=1000
        return int(instructions>=step_limit or time.perf_counter()>deadline)

    conn.set_progress_handler(progress,1000)
    try:
        plan=[{'id':row[0],'parent':row[1],'detail':row[3]} for row in conn.execute('EXPLAIN QUERY PLAN '+sql,parameters)]
        samples=[]
        last_rows=[]
        columns=[]
        # One warm-up followed by three monitored execute-and-fetch previews.
        for repetition in range(4):
            instructions=0
            deadline=time.perf_counter()+0.5
            started=time.perf_counter()
            cursor=conn.execute(sql,parameters)
            columns=[item[0] for item in cursor.description or []]
            last_rows=cursor.fetchmany(101)
            elapsed=(time.perf_counter()-started)*1000
            cursor.close()
            if repetition:
                samples.append({'milliseconds':elapsed,'vm_steps_estimate':instructions})
        rows=[]
        clipped=0
        for row in last_rows[:100]:
            values=[]
            for value in row:
                if isinstance(value,float) and not math.isfinite(value):
                    raise ValueError('Query produced a nonfinite numeric result.')
                if isinstance(value,bytes):
                    value='0x'+value.hex()
                if isinstance(value,str) and len(value)>1000:
                    value=value[:1000]+'…'
                    clipped+=1
                values.append(value)
            rows.append(values)
        return {'error':None,'indexes':indexes,'plan':plan,'columns':columns,'rows':rows,
                'truncated':len(last_rows)>100,'clipped_cells':clipped,'samples':samples,
                'median_ms':statistics.median(s['milliseconds'] for s in samples),
                'vm_steps_estimate':samples[-1]['vm_steps_estimate'],**storage}
    except (sqlite3.Error,ValueError,OverflowError) as error:
        message='Query exceeded its VM-instruction or time budget.' if 'interrupted' in str(error) else str(error)
        return {'error':message,'indexes':indexes,**storage}
    finally:
        conn.close()


def compare(data):
    data=validate(data)
    baseline=experiment(data['sql'],data['parameters'],[])
    indexed=experiment(data['sql'],data['parameters'],data['indexes'])
    comparison={'ordered_preview_equal':None,'unordered_preview_equal':None,'complete_unclipped_results':False}
    if not baseline['error'] and not indexed['error']:
        columns_match=baseline['columns']==indexed['columns']
        comparison['ordered_preview_equal']=columns_match and baseline['rows']==indexed['rows']
        comparison['unordered_preview_equal']=columns_match and Counter(json.dumps(r,sort_keys=True) for r in baseline['rows'])==Counter(json.dumps(r,sort_keys=True) for r in indexed['rows'])
        comparison['complete_unclipped_results']=not any(r['truncated'] or r['clipped_cells'] for r in (baseline,indexed))
    return {'request':data,'baseline':baseline,'indexed':indexed,'comparison':comparison,
            'sqlite_version':sqlite3.sqlite_version,'fixture':{'customers':1000,'orders':6000,'seed':202611},
            'measurement':{'warmups':1,'runs':3,'row_limit':100,'progress_interval':1000,'step_limit':300000,'time_limit_ms':500}}


EXAMPLES={
    'customer':{'sql':'SELECT id, created_at, amount_cents\nFROM orders\nWHERE customer_id = :customer\nORDER BY created_at, id\nLIMIT 25','parameters':{'customer':42},'indexes':['customer']},
    'status':{'sql':'SELECT id, created_at, amount_cents\nFROM orders\nWHERE status = :status AND created_at >= :since\nORDER BY created_at, id\nLIMIT 30','parameters':{'status':'paid','since':'2025-10-01'},'indexes':['status_date']},
    'amount':{'sql':'SELECT id, amount_cents\nFROM orders\nWHERE amount_cents >= :minimum\nORDER BY amount_cents DESC, id\nLIMIT 20','parameters':{'minimum':95000},'indexes':['amount']},
    'join':{'sql':'SELECT c.region, COUNT(*) AS order_count,\n       SUM(o.amount_cents) AS total_cents\nFROM orders AS o\nJOIN customers AS c ON c.id = o.customer_id\nWHERE o.status = :status\nGROUP BY c.region\nORDER BY total_cents DESC, c.region','parameters':{'status':'paid'},'indexes':['status_date']},
}

CATALOG={'schema':SCHEMA,'indexes':INDEXES,'examples':EXAMPLES,'functions':sorted(FUNCTIONS),
         'customers':1000,'orders':6000,'sqlite_version':sqlite3.sqlite_version}

