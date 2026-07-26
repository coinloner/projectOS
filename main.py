from app.llm.llm_client import LLMClient


def main():
    client = LLMClient()
    print(client.invoke("你好，请回复Hello"))


if __name__ == "__main__":
    main()
