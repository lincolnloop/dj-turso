import datetime as dt

import bs4
import pytest
from django.test import Client
from django.utils import timezone
from polls import models


@pytest.mark.django_db
def test_index_lists_published_questions(client: Client) -> None:
    question = models.Question.objects.create(
        question_text="What's up?", pub_date=timezone.now()
    )

    response = client.get("/polls/")

    assert response.status_code == 200
    soup = bs4.BeautifulSoup(response.content, "html.parser")
    assert [(link.text, link["href"]) for link in soup.select("#questions a")] == [
        ("What's up?", f"/polls/{question.pk}/")
    ]


@pytest.mark.django_db
def test_index_has_no_polls_until_a_question_is_published(client: Client) -> None:
    models.Question.objects.create(
        question_text="Future question.",
        pub_date=timezone.now() + dt.timedelta(days=30),
    )

    response = client.get("/polls/")

    assert response.status_code == 200
    soup = bs4.BeautifulSoup(response.content, "html.parser")
    assert soup.select_one("#no-polls").text == "No polls are available."


@pytest.mark.django_db
def test_detail_lists_choices(client: Client) -> None:
    question = models.Question.objects.create(
        question_text="What's up?", pub_date=timezone.now()
    )
    question.choice_set.create(choice_text="Not much")
    question.choice_set.create(choice_text="The sky")

    response = client.get(f"/polls/{question.pk}/")

    assert response.status_code == 200
    soup = bs4.BeautifulSoup(response.content, "html.parser")
    assert [label.text for label in soup.select("#vote label")] == [
        "Not much",
        "The sky",
    ]


@pytest.mark.django_db
def test_vote_counts_on_the_results_page(client: Client) -> None:
    question = models.Question.objects.create(
        question_text="What's up?", pub_date=timezone.now()
    )
    choice = question.choice_set.create(choice_text="Not much")
    question.choice_set.create(choice_text="The sky")

    response = client.post(f"/polls/{question.pk}/vote/", {"choice": choice.pk})

    assert response.status_code == 302
    assert response.headers["Location"] == f"/polls/{question.pk}/results/"
    response = client.get(response.headers["Location"])
    soup = bs4.BeautifulSoup(response.content, "html.parser")
    assert [item.text for item in soup.select("#results li")] == [
        "Not much -- 1 vote",
        "The sky -- 0 votes",
    ]


@pytest.mark.django_db
def test_vote_without_a_choice_asks_for_one(client: Client) -> None:
    question = models.Question.objects.create(
        question_text="What's up?", pub_date=timezone.now()
    )

    response = client.post(f"/polls/{question.pk}/vote/")

    assert response.status_code == 200
    soup = bs4.BeautifulSoup(response.content, "html.parser")
    assert soup.select_one("#error-message").text == "You didn't select a choice."
