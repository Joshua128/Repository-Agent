import ast  
from pathlib import Path

class astBuild():

    def __init__(self, path):
        self.path = Path(path)
        self.ast_tree = None
        self.parsed_files = {}

    def build_ast(self):
        for file in self.read_entire_dir(self.path):
            with open(file, 'r') as f:
                source_code = f.read()
                file_name = str(file).split("\\")[-1]
                print(f"File name: {file_name}")
                self.ast_tree = ast.parse(source_code)
                self.parsed_files[file_name] = self.ast_tree


    
    def iter_files(self):
        for file in self.path.rglob('*.py'):
            yield file
    
    
    def read_entire_dir(self,f_dir):
        for file in f_dir.iterdir():
            if file.is_file() and file.suffix == '.py':
                yield file
            elif file.is_dir():
                yield from self.read_entire_dir(file)
        

        
        
if __name__ == "__main__":
    
    path = r"C:\Users\joshu\OneDrive\Desktop\GitHub-Assistance\firstrepo"
 
    ast_builder = astBuild(path)

    ast_builder.build_ast()

    for i in range(len(ast_builder.parsed_files)):
        print(f"AST for file {i+1}:")
        print(ast.dump(ast_builder.parsed_files[i], indent=4)) 




    














