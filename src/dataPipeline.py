import os
from pathlib import Path
import sqlite3

#datapipeline to structure data

#A call is an edge to a function name
#SQLITE INTEGER INCREMENT KEY IS USED 


#first step in builing files (create repo table and file table for all ast parsed files)
def process_data(repo,db_connection):  #db_conn is actually cursor 
    #format is a dict with func def 
    repo_name = Path(repo).name
    db_connection.execute('''CREATE TABLE IF NOT EXISTS repo_table (
                          id INTEGER PRIMARY KEY AUTOINCREMENT,
                          repo_name TEXT,
                          path TEXT
                          )''')
    
    db_connection.execute('''INSERT INTO repo_table (repo_name, path) VALUES (?, ?)''', (repo_name, repo))
 
    #build_files(file_name, db_connection, repo_name, repo) 






def build_raw_text(file_name, file_lines, db_connection, repo_name, file_hash):
    db_connection.execute('''CREATE TABLE IF NOT EXISTS raw_text_table (
                          id INTEGER PRIMARY KEY AUTOINCREMENT,
                          file_id INTEGER,
                          file_hash TEXT,
                          function_name TEXT,
                          raw_text TEXT,
                          FOREIGN KEY (file_id) REFERENCES file_table(id)
                          )''')
    
    for func_name,lines in file_lines.items():
        raw_text = "\n".join(lines)
        db_connection.execute('''INSERT INTO raw_text_table (file_id, file_hash, function_name, raw_text)
                              VALUES ((SELECT id FROM file_table WHERE file_name = ?), ?, ?, ?)''', (file_name, file_hash, func_name, raw_text))

def build_functions(file_name, file_lines, db_connection, repo_name, file_hash):
    db_connection.execute('''CREATE TABLE IF NOT EXISTS function_table (
                          id INTEGER PRIMARY KEY AUTOINCREMENT,
                          file_id INTEGER,
                          file_hash TEXT,
                          function_name TEXT,
                          function_type TEXT,
                          line_start INTEGER,
                          line_end INTEGER,
                          FOREIGN KEY (file_id) REFERENCES file_table(id)
                          )''')
    
    for func_name,lines in file_lines.items():
        db_connection.execute('''INSERT INTO function_table (file_id, file_hash, function_name, function_type, line_start, line_end)
                              VALUES ((SELECT id FROM file_table WHERE file_name = ?), ?, ?, ?, ?, ?)''', (file_name, file_hash, func_name, "function", lines[0], lines[1]))




def build_files(file_name, db_connection, repo_name,repo_path):
    db_connection.execute('''CREATE TABLE IF NOT EXISTS file_table (
                          id INTEGER PRIMARY KEY AUTOINCREMENT,
                          repo_id INTEGER,
                          file_name TEXT,
                          file_path TEXT,
                          FOREIGN KEY (repo_id) REFERENCES repo_table(id)
                          )''')
    
    file_path = str(Path(repo_path) / file_name)
    
    db_connection.execute('''INSERT INTO file_table (repo_id, file_name, file_path) VALUES ((SELECT id FROM repo_table WHERE repo_name = ?), ?, ?)''', (repo_name, file_name, file_path))
    

    

#final part: Building edges to represent connections in the call graph 
#directed edge representes from A --> B represents A calls B. file_dics is an adjacency list of call graph



def build_edges(file_dics, db_connection, repo_name):
     db_connection.execute('''CREATE TABLE IF NOT EXISTS edge_table (
                              id INTEGER PRIMARY KEY AUTOINCREMENT,
                              repo_id INTEGER,
                              source_type TEXT,
                              source_id INTEGER,
                              target_type TEXT,
                              target_id INTEGER,
                              relationship_type TEXT,
                              FOREIGN KEY (repo_id) REFERENCES repo_table(id),
                              FOREIGN KEY (target_id) REFERENCES function_table(id)
                              )''')
    
     
     for file_name, called_functions in file_dics.items():
        for called_function in called_functions:
            db_connection.execute('''INSERT INTO edge_table(
                                      repo_id,source_type,source_id,target_type,target_id,relationship_type) VALUES (
                                      (SELECT id FROM repo_table WHERE repo_name = ?),
                                      'function',
                                      (SELECT id FROM function_table WHERE function_name = ?),
                                      'function',
                                      (SELECT id FROM function_table WHERE function_name = ?),
                                      'calls'
                                      )''', (repo_name, file_name, called_function))
     
            
    




    

    

    

    
            
       






