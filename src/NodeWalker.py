import ast 
from collections import defaultdict 


class NodeWalk(ast.NodeVisitor):
    def __init__(self):
        self.func_lines = defaultdict(tuple)
        self.func_stack = []
        self.func_dic = defaultdict(list)

    def visit_FunctionDef(self, node):
        
        self.func_lines[node.name] = (node.lineno, node.end_lineno)
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()


    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if self.func_stack:
                self.func_dic[self.func_stack[-1]].append(func_name)
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
            #print(f"Function call: {func_name} in function: {self.func_stack[-1] if self.func_stack else 'None'}")
            if self.func_stack:
                self.func_dic[self.func_stack[-1]].append(func_name)
        self.generic_visit(node)



    



