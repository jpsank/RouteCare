module Integrations
  class BaseClient
    def initialize(api_key: nil, **_kwargs)
      @api_key = api_key
    end

    def get(_url, **_options)
      {}
    end

    private

    attr_reader :api_key

    def configured?
      api_key.present?
    end
  end
end
