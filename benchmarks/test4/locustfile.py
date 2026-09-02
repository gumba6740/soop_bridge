from locust import HttpUser, constant, task


class MyUser(HttpUser):
    wait_time = constant(1.0)

    @task
    def locust(self):
        self.client.get("/streams/ecvhao/stats")
