# SEO Audit actor: bulk technical SEO checks over plain HTTP (no browser).
FROM apify/actor-python:3.12

COPY requirements.txt ./
# Install the Apify SDK explicitly: not guaranteed present in every base image tag.
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

CMD ["python3", "-m", "src"]
