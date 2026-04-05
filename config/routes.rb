Rails.application.routes.draw do
  devise_for :users
  root "home#index"
  get "auth/google/start", to: "calendar_oauth#google_start", as: :google_calendar_oauth_start
  get "auth/google/callback", to: "calendar_oauth#google_callback", as: :google_calendar_oauth_callback

  namespace :webhooks do
    post "twilio/sms", to: "twilio#sms"
    post "mailgun/inbound", to: "mailgun#inbound"
  end

  namespace :api do
    namespace :v1 do
      resources :patients do
        member do
          post :deactivate
        end
        collection do
          post :seed_demo
        end
      end
      resource :clinician_profile, only: %i[show update]
      resources :calendar_blocks, only: %i[index create update destroy]
      resources :calendar_connections, only: %i[index create destroy] do
        member do
          get :available_calendars
          post :select_calendar
          post :sync
          post :push_visits
        end
      end
      get "calendar_feed", to: "calendar_feeds#show", defaults: { format: :ics }
      resources :alerts, only: %i[index update]
      resources :messages, only: %i[index create] do
        member do
          post :approve
          post :select_suggestion
        end
        collection do
          post :create_inbound, path: "inbound"
        end
      end

      resource :schedule, only: %i[show create] do
        post :optimize
        post :approve
      end

      resources :visits, only: %i[index create update] do
        member do
          post :reschedule
        end
      end
    end
  end
  # Define your application routes per the DSL in https://guides.rubyonrails.org/routing.html

  # Reveal health status on /up that returns 200 if the app boots with no exceptions, otherwise 500.
  # Can be used by load balancers and uptime monitors to verify that the app is live.
  get "up" => "rails/health#show", as: :rails_health_check

  # Render dynamic PWA files from app/views/pwa/* (remember to link manifest in application.html.erb)
  # get "manifest" => "rails/pwa#manifest", as: :pwa_manifest
  # get "service-worker" => "rails/pwa#service_worker", as: :pwa_service_worker
end
