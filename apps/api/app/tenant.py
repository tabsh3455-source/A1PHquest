from sqlalchemy.orm import Query


def with_tenant(query: Query, model, user_id: int) -> Query:
    return query.filter(model.user_id == user_id)

