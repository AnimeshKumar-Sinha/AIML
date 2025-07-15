import os
from redisvl.extensions.router import Route, SemanticRouter
from redisvl.utils.vectorize import HFTextVectorizer

# Disable tokenizer parallelism
os.environ["TOKENIZERS_PARALLELISM"] = "false"

# Define routes
price_config = Route(
    name="price_config",
    references=[
        "total price checks by week",
        "price checks by source",
        "latest retail price"
    ],
    metadata={"domain": "pricing", "priority": 1},
    distance_threshold=0.7
)

price_analysis = Route(
    name="price_analysis",
    references=[
        "price trends by division",
        "competitor price comparison"
    ],
    metadata={"domain": "pricing", "priority": 2},
    distance_threshold=0.72
)

# Initialize the vectorizer
vectorizer = HFTextVectorizer(model="sentence-transformers/all-mpnet-base-v2")

# Initialize the SemanticRouter
router = SemanticRouter(
    name="price-router",
    vectorizer=vectorizer,
    routes=[price_config, price_analysis],
    redis_url="redis://localhost:6379",
    overwrite=True
)

# Test a query
route_match = router("To get the total price checks received for the latest and previous week, grouped by source and week description")
print(route_match)  # Expected: RouteMatch(name='price_config', distance=<some_value>)