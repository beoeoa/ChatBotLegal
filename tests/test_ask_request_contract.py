from api.models import AskRequest


def test_ask_request_initializes_conversation_persistence_flag():
    request = AskRequest(question="Câu hỏi thử nghiệm")

    assert request.pre_persisted_user_message is False
