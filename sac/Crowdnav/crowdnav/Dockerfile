FROM python:3.11

RUN apt-get update &&              \
  apt-get install -y             \
  build-essential              \
  git                          \
  libxerces-c-dev      \
  sumo sumo-tools sumo-doc

ENV SUMO_HOME /usr/share/sumo

# First cache dependencies
ADD ./setup.py /app/setup.py
RUN pip install ./app
# Add sources
ADD ./ /app/
WORKDIR /app
# CMD ["python","/app/forever.py"]
ENTRYPOINT ["python","/app/forever.py"]
