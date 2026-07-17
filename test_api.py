import requests
import json

def test():
    url = "http://localhost:8085/aspects?urn=urn:li:dataset:(urn:li:dataPlatform:postgres,paysim_raw_transactions,PROD)&aspect=schemaMetadata&version=0"
    try:
        res = requests.get(url)
        print(f"Status: {res.status_code}")
        print(res.text[:500])
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test()
