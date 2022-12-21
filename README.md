# hledger-charts

an exmple of how I used to host charts for my personal finances.
I've abandoned it now, because I haven't really looked at charts, but it was fun to setup.

powered by:
* [hledger](https://hledger.org) 
* [fly.io](https://fly.io) 
* [grafana cloud](https://grafana.com)

nice things about the setup:
* it's completely within free tiers (as of December 2022)
* no vendor lock - host your grafana and prometheus as you wish
* prometheus image has all the data backed on the build step, so it's read only

it's based on [Michael Walker](https://memo.barrucadu.co.uk/personal-finance.html)'s setup.
