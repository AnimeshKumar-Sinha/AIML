import requests

def read_file(path):

    url = "http://localhost:8000/read"

    return requests.post(url, json={"path": path}).json()


def fetch_web(url):

    response = requests.get(url)

    return response.text
