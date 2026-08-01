
#Used for testing purposes
from pathlib import Path

import astBuilder 
import NodeWalker
import dataPipeline 
import sqlite3

if __name__ == "__main__":
    
    path = r"C:\Users\joshu\OneDrive\Desktop\GitHub-Assistance\firstrepo"

    ast_builder = astBuilder.astBuild(path)

    ast_builder.build_ast()
    node_walkers = []
    for file, (ast_tree, source_code, file_hash) in ast_builder.parsed_files.items():
        print(f"AST for file {file[0]}:")
        #file_name = Path(file).name
        print(f"Hash for file {file}: {file_hash}")
        NodeParsing = NodeWalker.NodeWalk(file,file_hash,source_code)
        NodeParsing.visit(ast_tree)
        node_walkers.append(NodeParsing)
    #create a db
    db_connection = sqlite3.connect('call_graph.db') 
    cursor = db_connection.cursor()
    #print(node_walkers[0].func_dic)
    
    
    #add repo to db/create repo table if needed
    repo_id = dataPipeline.process_data(path,cursor)
    for walk in node_walkers:
        file_id = dataPipeline.build_files(walk.file_name, cursor, repo_id, path)
        dataPipeline.build_raw_text(file_id, walk.source_code, cursor, walk.file_hash)
        dataPipeline.build_functions(file_id, walk.func_lines, cursor)
  
    for walk in node_walkers:
        dataPipeline.build_edges(walk.func_dic, cursor, Path(path).name)
    db_connection.commit()
    















    """

    PRINTING DETAILS

    print("Function Dictionary:" )
    for parent, children in node_walkers[0].func_dic.items():
        print(f"  {parent}: {', '.join(children)}")
    

    print("Function lines : ")
    for func_name, (start_line, end_line) in node_walkers[0].func_lines.items():
        print(f"  {func_name}: {start_line}-{end_line}")
    

    for func_name, (start_line, end_line) in node_walkers[1].func_lines.items():
        print(f"Function '{func_name}' starts at line {start_line} and ends at line {end_line}.")
    """










        


