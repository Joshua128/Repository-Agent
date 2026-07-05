import astBuilder 
import NodeWalker

if __name__ == "__main__":
    
    path = r"C:\Users\joshu\OneDrive\Desktop\GitHub-Assistance\firstrepo"
 
    ast_builder = astBuilder.astBuild(path)

    ast_builder.build_ast()
    node_walkers = []
    for file, ast_tree in ast_builder.parsed_files.items():
        print(f"AST for file {file}:")
        NodeParsing = NodeWalker.NodeWalk()
        NodeParsing.visit(ast_tree)
        node_walkers.append(NodeParsing)
    



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










        


