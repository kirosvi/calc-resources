FROM python:3.9.15-alpine3.17

ADD requirements.txt .
RUN pip install -r requirements.txt

WORKDIR /app

ADD calc.py calc_config.yaml resources.j2 /app/

ENTRYPOINT ["/app/calc.py"]
