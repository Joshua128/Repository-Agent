import subprocess
class ProcessManager:

    def __init__(self, git_url):
        self.gitUrl = git_url 

    

    def pull_request(self):
        print("Pulling request from "  + self.gitUrl)


        try:

            subprocess.call(["git", "clone", self.gitUrl])


        except Exception as e:
            print("Error occurred while pulling request: " + str(e))



if __name__ == "__main__":
    git_url = "https://github.com/Joshua128/firstrepo.git"
    process_manager = ProcessManager(git_url)
    process_manager.pull_request()
