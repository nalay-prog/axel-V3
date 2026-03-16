# darwin/finalizer.py

def finalize(response: str) -> str:
    """
    Post-traitement final des réponses Darwin
    """
    if not response:
        return "Darwin n'a pas pu générer de réponse."

    return response.strip()
