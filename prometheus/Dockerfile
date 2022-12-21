FROM prom/prometheus:v2.39.1 as importer

COPY ./metrics.prom /tmp/metrics.prom
RUN promtool check metrics < /tmp/metrics.prom \
    && promtool tsdb create-blocks-from openmetrics /tmp/metrics.prom /prometheus


FROM prom/prometheus:v2.39.1

COPY ./web.yaml /etc/prometheus/web.yaml
RUN promtool check web-config /etc/prometheus/web.yaml

COPY ./prometheus.yaml /etc/prometheus/prometheus.yaml
RUN promtool check config /etc/prometheus/prometheus.yaml

COPY --from=importer /prometheus /prometheus

ENTRYPOINT [ "/bin/prometheus" ]
CMD        [ "--config.file=/etc/prometheus/prometheus.yaml", \
             "--web.config.file=/etc/prometheus/web.yaml", \
             "--storage.tsdb.path=/prometheus", \
             "--storage.tsdb.retention.time", "10y"]
