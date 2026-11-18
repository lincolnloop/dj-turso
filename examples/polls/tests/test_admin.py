import bs4
import pytest
from django.test import Client
from django.utils import timezone
from polls import models


@pytest.mark.django_db
def test_admin_lists_questions(admin_client: Client) -> None:
    models.Question.objects.create(question_text="What's up?", pub_date=timezone.now())

    response = admin_client.get("/admin/polls/question/")

    assert response.status_code == 200
    soup = bs4.BeautifulSoup(response.content, "html.parser")
    assert [link.text for link in soup.select("#result_list tbody th a")] == [
        "What's up?"
    ]


@pytest.mark.django_db
def test_admin_edits_a_question_with_its_choices(admin_client: Client) -> None:
    question = models.Question.objects.create(
        question_text="What's up?", pub_date=timezone.now()
    )
    question.choice_set.create(choice_text="Not much")

    response = admin_client.get(f"/admin/polls/question/{question.pk}/change/")

    assert response.status_code == 200
    soup = bs4.BeautifulSoup(response.content, "html.parser")
    assert soup.select_one("#id_question_text")["value"] == "What's up?"
    assert soup.select_one("#id_choice_set-0-choice_text")["value"] == "Not much"
